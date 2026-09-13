"""Long-document page discovery must preserve literal source evidence."""
import io

from PIL import Image
import pytest

from agentic_analytics.agent.tools.source_index import SourceIndexTools, _normalized, _page_match
from agentic_analytics.agent.tools.documents import DocumentTools
from app.context import AppContext


def test_find_note_beyond_default_inspection_and_preserve_bounded_continuation(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    buffer = io.BytesIO()
    pdf = PdfWriter()
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    for number in range(1, 36):
        page = pdf.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        title = "Revenue and Cost of Sales" if number == 34 else f"Unrelated note {number}"
        text = f"BT /F1 12 Tf 40 750 Td ({title}) Tj ET"
        if number == 34:
            text += " BT /F1 12 Tf 40 720 Td (Revenue 2025: 123456; Revenue 2024: 145678) Tj ET"
        stream = DecodedStreamObject()
        stream.set_data(text.encode())
        page[NameObject("/Contents")] = pdf._add_object(stream)
    pdf.write(buffer)
    context = AppContext(tmp_path / "runtime", None)
    try:
        workspace = context.create_workspace("PDF search", "generic")
        docs = DocumentTools(context.store, workspace["workspace_id"])
        uploaded = docs.upload_root / "long.pdf"
        uploaded.write_bytes(buffer.getvalue())
        source = docs.register_upload(uploaded)
        search = SourceIndexTools(context.store, workspace["workspace_id"])
        first = search.find_source_pages(source["source_id"], "Revenue and Cost of Sales", page_limit=30)
        assert not first["complete"] and first["next_start_page"] == 31 and first["matches"] == []
        result = search.find_source_pages(source["source_id"], "Revenue and Cost of Sales")
        assert result["complete"] and result["matches"][0]["page"] == 34
        assert "Revenue 2025: 123456" in result["matches"][0]["excerpt"]
        before = docs.source(source["source_id"])
        assert result["raw_sha256"] == before["raw_sha256"]
        assert context.store.workspace(workspace["workspace_id"])["version"] == 0
        other = context.create_workspace("Other", "generic")
        with pytest.raises(FileNotFoundError):
            SourceIndexTools(context.store, other["workspace_id"]).find_source_pages(source["source_id"], "Revenue")
    finally:
        context.pool.shutdown(wait=True)


def test_image_only_page_is_explicitly_not_searched(tmp_path):
    context = AppContext(tmp_path / "runtime", None)
    try:
        workspace = context.create_workspace("Scanned search", "generic")
        docs = context.documents(workspace["workspace_id"])
        uploaded = docs.upload_root / "scan.pdf"
        Image.new("RGB", (40, 40), "white").save(uploaded, format="PDF")
        source = docs.register_upload(uploaded)
        result = SourceIndexTools(context.store, workspace["workspace_id"]).find_source_pages(source["source_id"], "Revenue")
        assert result["image_only_pages"] == [1] and not result["complete"]
        assert result["matches"] == [] and result["warnings"]
    finally:
        context.pool.shutdown(wait=True)


def _text_pdf(pages):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer, buffer = PdfWriter(), io.BytesIO()
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    for lines in pages:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        instructions = []
        for index, line in enumerate(lines):
            escaped = line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
            instructions.append(f'BT /F1 10 Tf 40 {750-index*15} Td ({escaped}) Tj ET')
        stream = DecodedStreamObject()
        stream.set_data('\n'.join(instructions).encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(buffer)
    return buffer.getvalue()


def test_late_sector_heading_outranks_scattered_words_and_preserves_page_windows(tmp_path):
    pages = [[f'Unrelated source note {page}'] for page in range(1, 87)]
    pages[1] = ['Loans are described in this accounting policy.', *['Other accounting policies.'] * 15,
                'The banking sector is regulated.']
    pages[2] = ['4.8 Profit distribution and taxation', 'Loans generate interest income.']
    pages[83] = ['5.8 Distribution of cash loans by economic sectors', 'Current period   Prior period',
                 'Manufacturing   111   100', 'Construction   222   200', 'Printed page 17']
    raw = _text_pdf(pages)
    context = AppContext(tmp_path / 'runtime', None)
    try:
        workspace = context.create_workspace('Sector note', 'generic')
        docs = context.documents(workspace['workspace_id'])
        upload = docs.upload_root / 'long-sector-note.pdf'
        upload.write_bytes(raw)
        source = docs.register_upload(upload)
        search = SourceIndexTools(context.store, workspace['workspace_id'])
        for query in ('loans by sector', 'sectoral distribution of loans'):
            result = search.find_source_pages(source['source_id'], query)
            match = result['matches'][0]
            assert result['complete'] and result['next_start_page'] is None
            assert match['page'] == 84 and match['match_type'] == 'heading'
            assert match['all_query_terms']
            assert 'Manufacturing 111 100' in match['excerpt']
            assert 'Printed page 17' in match['excerpt']
            assert result['page_number_basis'] == 'physical_pdf_1_based'
            assert result['recovery']['arguments']['page_numbers'][0] == 84
            assert match['term_variants']['sector' if query == 'loans by sector' else 'sectoral'] == ['sectors']
        first = search.find_source_pages(source['source_id'], 'loans by sector', page_limit=30)
        assert first['searched_pages'] == list(range(1, 31))
        assert not first['complete'] and first['next_start_page'] == 31
        assert all(match['page'] <= 30 for match in first['matches'])
        middle = search.find_source_pages(source['source_id'], 'loans by sector', start_page=31, page_limit=40)
        assert middle['matches'] == [] and middle['next_start_page'] == 71
        tail = search.find_source_pages(source['source_id'], 'loans by sector', start_page=71)
        assert tail['matches'][0]['page'] == 84 and tail['next_start_page'] is None
        assert not tail['complete']  # This call did not search pages 1-70.
        assert docs.raw_source_bytes(source['source_id']) == raw
        assert result['raw_sha256'] == docs.source(source['source_id'])['raw_sha256']
        assert context.store.workspace(workspace['workspace_id'])['version'] == 0
    finally:
        context.pool.shutdown(wait=True)


def test_loan_allocation_note_is_a_search_candidate_when_sector_table_is_missing(tmp_path):
    raw = _text_pdf([
        ['5.1.5.6 Allocation of loans by customers',
         'Not prepared in compliance with the reporting requirements.'],
        ['5.1.11 Sectoral distribution of investments', 'Banks 100'],
    ])
    context = AppContext(tmp_path / 'runtime', None)
    try:
        workspace = context.create_workspace('Loan note', 'generic')
        docs = context.documents(workspace['workspace_id'])
        upload = docs.upload_root / 'loan-note.pdf'
        upload.write_bytes(raw)
        source = docs.register_upload(upload)
        result = SourceIndexTools(context.store, workspace['workspace_id']).find_source_pages(
            source['source_id'], 'sectoral distribution of loans')
        assert result['matches'][0]['page'] == 1
        assert result['suggested_inspection']['page_numbers'][0] == 1
        assert result['matches'][0]['matched_terms'] == ['distribution', 'loans']
        assert 'Not prepared in compliance' in result['matches'][0]['excerpt']
        assert not result['matches'][0]['all_query_terms']
    finally:
        context.pool.shutdown(wait=True)


def test_excerpt_is_contiguous_and_partial_investment_match_does_not_claim_loan_coverage():
    text = '\n'.join(['Loans were classified by maturity.', *['A separate accounting policy follows.'] * 20,
                      '8.4 Sectoral distribution of investments', 'Manufacturing 100', 'Technology 200'])
    match = _page_match(text, 'sectoral distribution of loans', ['sectoral', 'distribution', 'loans'])
    assert match['all_query_terms'] and match['match_type'] == 'scattered_terms'
    assert match['local_matched_terms'] == ['sectoral', 'distribution']
    assert 'Loans were classified' not in match['excerpt']
    assert match['excerpt'] == '\n'.join(text.splitlines()[match['line_start']-1:match['line_end']])
    partial = _page_match('8.4 Sectoral distribution of investments\nManufacturing 100',
                          'sectoral distribution of loans', ['sectoral', 'distribution', 'loans'])
    assert not partial['all_query_terms'] and partial['match_type'] == 'partial_terms'
    assert partial['matched_terms'] == ['sectoral', 'distribution']


def test_actual_scope_note_outranks_contents_and_word_boundaries_remain_meaningful():
    query = 'consolidated credit risk'
    terms = query.split()
    contents = _page_match('SECTION ONE Page No:\nConsolidated Credit Risk 55', query, terms)
    note = _page_match('4.2 Consolidated Credit Risk\nThis disclosure has not been prepared for the interim period.', query, terms)
    assert contents['match_type'] == 'contents' and note['score'] > contents['score']
    assert 'not been prepared' in note['excerpt']
    assert _page_match('Shareholdings and shareholder reports', _normalized('share'), ['share']) is None
    assert _page_match('1.2 Loan receivables', 'loans', ['loans'])['matched_terms'] == ['loans']


def test_wrapped_heading_preserves_both_lines_and_clipped_excerpt_line_end():
    phrase, terms = 'sectoral distribution of loans', ['sectoral', 'distribution', 'loans']
    text = '\n'.join(['7.3 Distribution of cash loans', 'by economic sectors', 'Manufacturing 100', 'Construction 200'])
    wrapped = _page_match(text, phrase, terms)
    assert wrapped['match_type'] == 'local_context' and wrapped['all_query_terms']
    assert wrapped['local_matched_terms'] == terms
    assert wrapped['excerpt'] == text
    assert wrapped['line_start'] == 1 and wrapped['line_end'] == 4
    long = '7.3 Sectoral distribution of loans\n' + '\n'.join('Literal source line ' + str(i) + ': ' + 'x' * 700 for i in range(8))
    clipped = _page_match(long, phrase, terms)
    assert clipped['excerpt_truncated'] and len(clipped['excerpt']) == 2400
    assert clipped['line_end'] == len(clipped['excerpt'].splitlines()) < len(long.splitlines())
    assert long.startswith(clipped['excerpt'])
