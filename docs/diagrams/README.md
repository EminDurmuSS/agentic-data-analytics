# README mimari şemaları

- `system-architecture.puml` ve `system-architecture.mmd`: Genel mimarinin PlantUML ve Mermaid kaynakları.
- `agent-decision-flow.puml` ve `agent-decision-flow.mmd`: Agent karar döngüsünün PlantUML ve Mermaid kaynakları.
- `medallion-flow.puml`: Mevcut veri hattının Bronze/Silver/Gold sorumluluklarına kavramsal eşlemesi.
- Aynı adlardaki SVG dosyaları PlantUML çıktısıdır; README bunları göreli yollarla gösterir.

Medallion kavramının referansı: [Databricks — What is Medallion Architecture?](https://www.databricks.com/blog/what-is-medallion-architecture). Şemalar bu proje için çizilmiştir.

## Yeniden üretme

Java, Graphviz (`dot`) ve PlantUML 1.2025.2 kullanılarak repo kökünden:

```sh
java -Djava.awt.headless=true -jar /path/to/plantuml-1.2025.2.jar -charset UTF-8 -tsvg docs/diagrams/system-architecture.puml docs/diagrams/medallion-flow.puml docs/diagrams/agent-decision-flow.puml
```

PlantUML JAR dosyası [resmî sürüm sayfasından](https://github.com/plantuml/plantuml/releases/tag/v1.2025.2) edinilebilir. JAR dosyası repoya eklenmez. Mimari değiştiğinde `.mmd` ve `.puml` kaynaklarını birlikte güncelleyin; SVG'leri yeniden üretin. README, Mermaid eklentisi gerektirmeyen SVG görsellerini kullanır.
