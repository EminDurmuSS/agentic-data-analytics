# Kaynak dosya kayıtları

Bu dosyalar 7 Eylül 2026 tarihinde `/Users/edurmus/Downloads` konumundan kopyalandı veya açıldı. Kaynak dosyalar yerinde bırakıldı.

| Kaynak | Projedeki hedef | SHA-256 |
| --- | --- | --- |
| `KKB_Veri_Paketi (1).zip` | `data_pipeline/` olarak açıldı | `65529399f9e875804ebf7c70f06fe44628aff740d5dd6fce65958afde3a0a6be` |
| `KKB_Verileri_Dogrulanmis (3).ipynb` | İlk notebook kaynağı, daha sonra güncel veri notebook'u ile değiştirildi | `1bced21dc4a59508634695f9aa31d0cc68a384ae0f34ea24e2df7648c4b2430a` |
| `KKB_Derin_Arastirma.docx` | `docs/research/KKB_Derin_Arastirma.docx` | `3e8e6d44160e30b5ce0c61963653aa0050092052a9c5b386e8886631823e1d69` |
| `BDDK_Indirme_Araci.py` | `tools/BDDK_Indirme_Araci.py` | `f061af7dafb209810fc8a7da7144baea875d913fd5891893b6608bbd05c74ff8` |

İlk ZIP'in manifesti içe aktarma sırasında kontrol edildi. Güncel
`data_pipeline/FILE_SHA256.json`, repoya sonradan eklenen bütün resmî kaynakları
da kapsayacak şekilde yeniden üretilir.

Canlı BDDK verileri bu bilgisayarda TLS doğrulaması açık tutularak yeniden
indirildi. Aylık tam kapsam `data_pipeline/bddk/monthly_all_groups/`, haftalık
tam kapsam `data_pipeline/bddk/weekly_all_groups/`, FinTürk kapsamı ise
`data_pipeline/bddk/finturk_all_groups_all_cities/` altında bulunur. İlk
tüketici kredisi denemesi `monthly_consumer_credit_sector/` altında tarihsel
kanıt olarak korunur. Bu dosyalar `Downloads` içe aktarımı değildir.

Güncel notebook `tools/build_data_status_notebook.py` ile doğrulanmış DuckDB
üzerinden üretilir. Eski 25 serilik gömülü snapshot güncel kapsamı temsil
etmediği için notebook içinde tutulmaz.
