# Veri keşfi notebookları

| Notebook | Amaç |
| --- | --- |
| [KKB_Verileri_Dogrulanmis.ipynb](KKB_Verileri_Dogrulanmis.ipynb) | Repoyla taşınan seçilmiş kaynak paketinin kapsamı ve doğrulama sorguları |
| [lakehouse_veri_kesfi_ve_iliskiler.ipynb](lakehouse_veri_kesfi_ve_iliskiler.ipynb) | Lakehouse envanteri, eksik gözlemler, kaynak ilişkileri ve örnek analizler |

Notebookları Python 3.12 proje ortamıyla açın. Kod, repo kökünü bulunduğu
dizinin üst klasörlerinden bulur ve mevcut DuckDB dosyasını salt okunur açar.
Veritabanı yoksa önce [kurulum adımlarını](../docs/DEVELOPMENT.md) uygulayın.

Kaydedilmiş çıktılar üretildikleri veri sürümünü gösterir. Seçilmiş kaynak
paketi ile yerelde tamamlanan EVDS yayını farklı kapsamlardır;
[veri rehberi](../docs/DATA.md) bu ayrımı açıklar. Güncel sonuç için hücreleri
yeniden çalıştırın. Notebook çıktısı agent'ın canlı cevap doğruluğunu ölçmez.

Durum notebookunu yeniden üretmek için repo kökünden:

```bash
.venv/bin/python -m tools.build_data_status_notebook
```

Üretici yalnız `notebooks/KKB_Verileri_Dogrulanmis.ipynb` dosyasına yazar.
