"""Real PDF research retains page identity beyond shortened inspection previews."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import duckdb

from agentic_analytics.agent.tools.documents import DocumentTools
from agentic_analytics.agent.tools.pdf_research import pdf_passages, pdf_title
from agentic_analytics.lakehouse.store import LakehouseStore


def text_pdf(pages, title=None):
    def escaped(value):
        return value.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
    kids = ' '.join(f'{4 + 2 * i} 0 R' for i in range(len(pages)))
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               f'<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>'.encode(),
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    for i, text in enumerate(pages):
        stream = ('BT /F1 9 Tf 32 760 Td\n' + '\n'.join(f'({escaped(line)}) Tj 0 -10 Td' for line in text.splitlines()) + '\nET').encode()
        objects.extend([f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>'.encode(),
                        b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream'])
    if title:
        objects.append(f'<< /Title ({escaped(title)}) >>'.encode())
    data, offsets = b'%PDF-1.4\n', []
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f'{index} 0 obj\n'.encode() + obj + b'\nendobj\n'
    xref = len(data)
    data += f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode()
    data += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets)
    info = f' /Info {len(objects)} 0 R' if title else ''
    return data + f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R{info} >>\nstartxref\n{xref}\n%%EOF\n'.encode()


class PdfResearchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        with duckdb.connect(str(self.root / 'seed.duckdb')) as connection:
            connection.execute('CREATE TABLE seed(value INTEGER)')
        self.store = LakehouseStore(self.root / 'store')
        snapshot = self.store.publish_snapshot(self.root / 'seed.duckdb')
        self.store.create_workspace(snapshot['snapshot_id'], 'workspace_research')
        self.docs = DocumentTools(self.store, 'workspace_research')

    def test_research_returns_full_target_page_after_12000_characters_and_real_document_title(self):
        filler = '\n'.join('Operations and risk management narrative without an ownership list.' for _ in range(55))
        owners = '\n'.join(f'BANK {letter}' for letter in 'ABCDEFGHI')
        target = '2025 SHAREHOLDERS\n' + owners + '\nMEMBERS\nMEMBER ONLY COMPANY'
        raw = text_pdf(['2025 ANNUAL REPORT\nExample Registry', 'CONTENTS\n07 SHAREHOLDERS\n07 MEMBERS',
                        filler, filler, filler, filler, target])
        url = 'https://registry.example/annual-2025.pdf'
        self.docs.web_search = lambda *args, **kwargs: {'status': 'ok', 'results': [{'url': url, 'title': 'DETAILS'}]}
        with patch('agentic_analytics.agent.tools.documents.fetch_public_url', return_value=(raw, 'application/pdf', url)):
            researched = self.docs.research_web('2025 shareholders ownership', limit=1)
        self.assertEqual(researched['status'], 'ok', researched)
        card = researched['sources'][0]
        self.assertEqual(card['title'], '2025 ANNUAL REPORT Example Registry')
        self.assertEqual(card['title_basis'], 'pdf_cover')
        self.assertEqual(card['matched_pages'][0], 7)
        self.assertEqual(card['passages'][0]['text'], target)
        self.assertFalse(card['passages'][0]['content_truncated'])
        self.assertTrue(card['content_truncated'])
        self.assertEqual(card['suggested_inspection']['page_numbers'][0], 7)
        self.assertEqual(card['passages'][0]['page_number_basis'], 'physical_pdf_1_based')
        self.assertEqual(self.docs.raw_source_bytes(card['source_id']), raw)
        inspected = self.docs.inspect_source(source_id=card['source_id'])
        self.assertNotIn('BANK I', inspected['text'], 'The regression requires the answer to lie beyond the short preview')
        self.assertEqual(card['raw_sha256'], inspected['raw_sha256'])
        self.assertLess(card['content'].index('BANK I'), card['content'].index('MEMBERS'))
        cache = json.loads((self.docs._directory(card['source_id']) / 'inspection.json').read_text())
        self.assertEqual(cache['pages'][6]['text'], target)

    def test_metadata_title_wins_over_link_label_and_truncated_passage_requires_page_read(self):
        lines = ['2025 SHAREHOLDERS'] + [f'BANK {i:03d}: a separately disclosed institution name' for i in range(120)]
        raw = text_pdf(['2025 ANNUAL REPORT', '\n'.join(lines)], title='Example Registry: 2025 Ownership Report')
        url = 'https://registry.example/report.pdf'
        self.docs.web_search = lambda *args, **kwargs: {'status': 'ok', 'results': [{'url': url, 'title': 'READ MORE'}]}
        with patch('agentic_analytics.agent.tools.documents.fetch_public_url', return_value=(raw, 'application/pdf', url)):
            card = self.docs.research_web('2025 ownership shareholders', limit=1)['sources'][0]
        self.assertEqual(card['title'], 'Example Registry: 2025 Ownership Report')
        self.assertEqual(card['title_basis'], 'pdf_metadata')
        passage = card['passages'][0]
        self.assertTrue(passage['content_truncated'])
        self.assertLessEqual(len(passage['text']), 3000)
        self.assertEqual(card['suggested_inspection'], {'source_id': card['source_id'], 'page_numbers': [2]})
        self.assertIn('before claiming a complete list', card['next_step'])

    def test_page_passages_never_stitch_members_beneath_an_ownership_heading(self):
        pages = [{'page': 1, 'text': 'CONTENTS\nSHAREHOLDERS\nMEMBERS'},
                 {'page': 2, 'text': 'SHAREHOLDERS\nOWNER BANK\nMEMBERS\nMEMBER ONLY BANK'},
                 {'page': 3, 'text': 'HISTORY\nFounded by a group of institutions.'}]
        matches = lambda value: int('SHAREHOLDERS' in value)
        passages = pdf_passages(pages, matches)
        self.assertEqual(passages[0]['page'], 2)
        self.assertEqual(passages[0]['text'], pages[1]['text'])
        self.assertNotIn('Founded', passages[0]['text'])
        self.assertEqual(pdf_title({}, [], 'annual.pdf'), {'title': 'annual.pdf', 'title_basis': 'registered_filename'})
