"""A terminal delivery must never be a dead-end: never empty, never a lone
"header:" with no body. This is the safety net behind the "model asla tıkanıp
boş kalmasın" requirement — it guarantees a useful message without fabricating.
"""
import pytest

from agentic_analytics.agent.runtime import _has_message_body


@pytest.mark.parametrize("message", [
    "",
    "   ",
    "\n\n",
    None,
    "1 Mayıs 2026 - 26 Haziran 2026 aralığında:",          # lone dangling header
    "2026-03 için:\n\n",                                    # header + only blank
    "Kaynaklar:\n\nEk not:",                                # only header labels
])
def test_bodyless_messages_are_rejected(message):
    assert _has_message_body(message) is False


@pytest.mark.parametrize("message", [
    "Sonuç hazır.",
    "1 Mayıs 2026 - 26 Haziran 2026 aralığında:\n\n- Taşıt kredisi faiz oranı: %39.67 → %51.85.",
    "İstenen işlem tamamlanamadı; yeni bir analiz üretilmedi.",
    "9 kayıt, grup ve dönemleriyle tabloda gösteriliyor.",
])
def test_messages_with_real_body_are_accepted(message):
    assert _has_message_body(message) is True
