"""Bounded structural HTML tables with explicit spanning-cell provenance."""
from html.parser import HTMLParser


class HTMLTableExtractor(HTMLParser):
    def __init__(self, max_rows=5001, max_columns=64):
        super().__init__(convert_charrefs=True)
        self.max_rows, self.max_columns = max_rows, max_columns
        self.tables, self.warnings = [], []
        self.depth, self.rows, self.row, self.cell = 0, None, None, None
        self.in_head = False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.depth += 1
            if self.depth == 1:
                self.rows = []
            else:
                self.warnings.append({"code": "NESTED_TABLE_REVIEW_REQUIRED"})
        if self.depth != 1:
            return
        if self.cell is not None and tag in {"br", "p", "div", "li", "section"}:
            self.cell["text"].append("\n")
        if tag == "thead":
            self.in_head = True
        elif tag == "tr":
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            attrs = dict(attrs)
            try:
                colspan, rowspan = int(attrs.get("colspan", 1)), int(attrs.get("rowspan", 1))
                if not 1 <= colspan <= self.max_columns or not 1 <= rowspan <= self.max_rows:
                    raise ValueError
            except ValueError:
                self.warnings.append({"code": "INVALID_TABLE_SPAN"})
                colspan, rowspan = 1, 1
            self.cell = {"text": [], "colspan": colspan, "rowspan": rowspan, "header": tag == "th" or self.in_head}

    def handle_data(self, data):
        if self.depth == 1 and self.cell is not None:
            self.cell["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "table":
            if self.depth == 1 and self.rows:
                try:
                    self.tables.append(self._expand(self.rows))
                except ValueError as exc:
                    self.warnings.append({"code": "AMBIGUOUS_TABLE", "message": str(exc)})
                self.rows = self.row = self.cell = None
            self.depth = max(0, self.depth - 1)
            return
        if self.depth != 1:
            return
        if self.cell is not None and tag in {"p", "div", "li", "section"}:
            self.cell["text"].append("\n")
        if tag == "thead":
            self.in_head = False
        elif tag in {"td", "th"} and self.cell is not None:
            # Whitespace inside a visual line is harmless to normalize. Line
            # boundaries separate possible observations and must survive so
            # downstream numeric parsing never invents a concatenated amount.
            self.cell["text"] = "\n".join(" ".join(line.split()) for line in "".join(self.cell["text"]).splitlines() if line.strip())
            self.row.append(self.cell)
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if len(self.rows) >= self.max_rows:
                raise ValueError("HTML table exceeds its row bound.")
            self.rows.append(self.row)
            self.row = None

    def _expand(self, rows):
        cells, spans, header_count, in_headers = {}, [], 0, True
        review = False
        for index, row in enumerate(rows):
            is_header = bool(row) and all(cell["header"] for cell in row)
            if in_headers and is_header:
                header_count += 1
            else:
                in_headers = False
            column = 0
            for cell in row:
                while (index, column) in cells:
                    column += 1
                width, height = cell["colspan"], cell["rowspan"]
                if column + width > self.max_columns or index + height > self.max_rows:
                    raise ValueError("HTML spanning cell exceeds table bounds.")
                if width > 1 or height > 1:
                    spans.append({"source_row": index + 1, "source_column": column + 1,
                                  "rowspan": height, "colspan": width, "text": cell["text"], "header": cell["header"]})
                    review |= not cell["header"]
                for dr in range(height):
                    for dc in range(width):
                        position = (index + dr, column + dc)
                        if position in cells:
                            raise ValueError("Overlapping HTML spanning cells.")
                        cells[position] = cell["text"] if cell["header"] or dr == dc == 0 else None
                column += width
        if not cells:
            raise ValueError("Empty HTML table.")
        width = max(column for _, column in cells) + 1
        height = max(row for row, _ in cells) + 1
        return {"rows": [[cells.get((row, column)) for column in range(width)] for row in range(height)],
                "header_rows": max(1, header_count), "spans": spans,
                "requires_review": review or any(warning["code"] in {"INVALID_TABLE_SPAN", "NESTED_TABLE_REVIEW_REQUIRED"} for warning in self.warnings)}
