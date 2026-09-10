# Veri işletim araçları

Bu klasör kaynak indirme, toplama kuyruğu ve yerel yayın bakımı içindir.
Agent'ın çalışırken çağırdığı araçlar
[agentic_analytics/agent/tools/](../agentic_analytics/agent/tools/) altındadır.

| İş | Araçlar |
| --- | --- |
| BDDK kaynak indirme | `BDDK_Indirme_Araci.py`, `BDDK_Haftalik_Indirme_Araci.py`, `BDDK_FinTurk_Indirme_Araci.py` |
| EVDS kaynak indirme | `EVDS_Manifest_Indirme_Araci.py`, `EVDS_Talep_Uzerine_Indirme_Araci.py` |
| EVDS tam geçmiş | `evds_bulk_collection.py`, `publish_evds_bulk.py`, `complete_evds_history.py` |
| Önceki tek seri kuyruğu | `evds_collection_queue.py` |
| Ek kaynaklar | `TBB_Kredi_Raporlari_Indirme_Araci.py`, `TUIK_Il_Konut_Satislari_Indirme_Araci.py` |
| Bakım ve doğrulama | `merge_bddk_weekly_snapshots.py`, `validate_agent_lakehouse.py`, `build_data_status_notebook.py` |

Komutları repo kökünden proje Python ortamıyla çalıştırın:

```bash
.venv/bin/python -m tools.evds_bulk_collection status
.venv/bin/python -m tools.complete_evds_history --help
```

Veri kapsamı ve indirme/yayın komutları [veri rehberinde](../docs/DATA.md),
dönüştürme betikleri [data_pipeline/](../data_pipeline/README.md),
agent değerlendirmeleri [evals/](../evals/) altındadır.
