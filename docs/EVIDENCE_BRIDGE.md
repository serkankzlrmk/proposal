# Evidence Bridge — Sightline Entegrasyon Notu (MIGRATION NOTE)

> **Durum:** Proposal Studio, Sightline'ın `reliefweb_api/` tool'larını **kod
> kopyalamadan** çalışma zamanında kullanıyor (`engine/evidence.py` köprüsü).
> Bu not, ileride Proposal Studio Sightline'a modül olarak taşındığında
> yapılacakları tarif eder.

## Şu an nasıl çalışıyor

- `engine/evidence.py` → `SIGHTLINE_ROOT` (varsayılan `~/Documents/reliefweb/RedAgent`)
  sys.path'e ekler, `reliefweb_api.reliefweb` + `reliefweb_api.hdx_tools` import eder.
- Tool'lar LangChain `StructuredTool` objeleri → `.invoke({...})` ile çağrılır.
- HDX client singleton'ı `init_hdx_tools(app_identifier=...)` ile başlatılır;
  key, Sightline'ın `.env`'inden okunur (bu repoya kopyalanmaz).
- Sightline yoksa / import hata verirse her çağrı `None` döner — proposal
  pipeline asla kırılmaz (zero-crash).
- `generate_narrative_sections` her bölüm prompt'una canlı kanıt enjekte eder:
  `EVIDENCE FROM LIVE SOURCES (cite with [ref: SIGHTLINE_<SOURCE>])`.

## Taşıma anında yapılacaklar (MIGRATION CHECKLIST)

1. **`engine/evidence.py` silinir** — Sightline içinde `reliefweb_api` zaten
   aynı process'te; doğrudan `from reliefweb_api import ...` kullanılır.
2. **`engine/generator.py`** içindeki `collect_evidence`/`evidence_to_prompt`
   çağrıları doğrudan import'a çevrilir; `_ascii_country`/`_country_code_for`
   yardımcıları Sightline'ın `country_codes.py`'siyle birleştirilir.
3. **`SIGHTLINE_ROOT` env var'ı** artık gereksiz — kaldırılır.
4. **HDX init** — Sightline `server.py` zaten `init_hdx_tools()` çağırıyor;
   köprüdeki init bloğu silinir.
5. **Bağımlılıklar** — `requests`, `langchain`, `langchain-core`, `pypdf`
   proposal venv'inden çıkar (Sightline'ın requirements'ında zaten var).
6. **Citation registry** — `[ref: SIGHTLINE_<SOURCE>]` formatı, Sightline
   kaynak ID'leriyle (`OCHA_SITREP_2026_01` vb.) birleştirilir; `format_source_id`
   helper'ı ortak kullanılır.

## Neden köprü (taşımadan bağlama)?

- İki repo bağımsız kalır (ARCHITECTURE.md kuralı).
- Sightline'ın tool'ları canlı ve production'da test edilmiş — kopyalamak
  çift bakım yaratır.
- Taşıma anında tek dosya silinir, gerisi import path değişikliği.
