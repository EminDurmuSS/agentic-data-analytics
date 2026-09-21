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

## BIST Banka Endeksi aylık kapanış aktarımı

`import_bist_xbank.py`, Borsa İstanbul'un anonim erişilen
[konsolide fiyat endeksleri dosyasındaki](https://www.borsaistanbul.com/datum/TR_PayEndeksleriFiyat.zip)
XBANK / BIST BANKA / TL satırlarını kullanır. Varsayılan işlem yalnız incelemedir:

```bash
.venv/bin/python -m tools.import_bist_xbank --year 2025
```

Yayın için önce arayüzde ayrı bir KKB finans çalışma alanı oluşturun. Aşağıdaki
`workspace_...` yerine o alanın gerçek kimliğini verin. Docker'daki kodu veya
salt okunur temel DuckDB dosyasını değiştirmeden, mevcut kalıcı runtime store'a
aktarım yapılabilir:

```bash
docker compose exec -T agent-app python - \
  --publish --year 2025 \
  --store /app/.lakehouse-runtime/app/lakehouse \
  --workspace-id workspace_... < tools/import_bist_xbank.py
```

- `--publish` açıkça verilmedikçe kayıt/yayın yapılmaz. Bu bir operatör aracıdır;
  modele yeni bir otomatik yayın yetkisi vermez.
- Tamamlanmış yılda tam 12 farklı ay, doğru endeks/para birimi, özgün sayısal
  hücreler ve beklenen dosya/sütun yapısı doğrulanır. Eksik veya yinelenen ay,
  formül, başka endeks ya da günlük veriye dönüşmüş dosya yayını durdurur.
- Kaynak ay sonuna yakın gerçek işlem gününü verebilir. `month` ay etiketidir;
  `closing_date` kaynak tarihidir. Tarih takvim ay sonuna taşınmaz; değerler
  toplanmaz, ortalanmaz, interpolasyon yapılmaz.
- Ölçü `index`, tür `index`, ölçek 1'dir. TL fiyat endeksi seçilmiştir; kapanış
  değeri para tutarı, getiri endeksi veya BIST Mali Endeksi değildir.
- Ham ZIP, içindeki XLSX'in hash'i, kaynak sayfa/URL, sayfa adı ve hücre adresleri
  kalıcı kaynak paketinde saklanır. Aynı arşivi aynı aktarım alanına tekrar
  yayınlamak yeni bir kopya üretmez. Değişmiş kaynaklar yeni sürümdür; eski
  sürümleri silme/değiştirme otomatik yapılmaz.
- Ortak release yeni **KKB finans** çalışma alanlarına bağlanır. Önceden açılmış
  alanlar kendi sürümlerinde kalır; yeniden test için yeni alan açın. Veriler
  `agent-runtime` volume'undadır; normal `down/up` ile korunur, `down -v` ile
  silinir. Git commit'i runtime verisini başka makineye taşımaz.
