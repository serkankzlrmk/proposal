# Proposal — GMS Grant Proposal Engine

Sightline'dan **bağımsız** bir proje: insani yardım hibe teklifi (proposal) üretim
pipeline'ı. AI destekli ToC → Logframe → Narrative → Blind Verifier → Typst PDF.

## Mimari İlkeler

- **Sightline bağımsızlığı**: bu repo Sightline'ı import etmez, değiştirmez.
  Geliştirme aşamaları ayrı yürür. (Detay: `docs/ARCHITECTURE.md`)
- **İleride entegrasyon**: veri kaynağı katmanı (`data_sources/`) Sightline'ın
  `reliefweb_api/` deseniyle uyumlu tasarlanır; taşıma anında bütün olarak
  Sightline'a ayrı modül olarak girebilir.
- **UI dili**: İngilizce (placeholder/hata/badge dahil).
- **LLM-Ops**: her AI aksiyonu trace'lenir; token kullanımı kalıcı ledger'da.

## Kurulum

```bash
uv venv --python 3.11
uv pip install --python .venv/bin/python -r requirements.txt
```

## Test

```bash
.venv/bin/python -m pytest tests/ -v
```

## Çalıştırma

```bash
.venv/bin/python app.py
# → http://127.0.0.1:5002
```

## Yapı

```
app.py                  # Flask sunucusu (:5002)
config.py               # Ortam ayarları
db.py                   # SQLite CRUD + audit logları
engine/
  donor_rules.py        # OCHA / USAID / EU şablon + karakter limitleri
  generator.py          # AI ToC, 4x4 Logframe, Narrative
  verifier.py           # Blind Verifier (LLM-as-a-Judge)
  advisor.py            # Interaktif AI danışman + patch önerileri
typst_engine/           # <10ms Typst PDF derleyici
blueprints/proposal_api.py  # REST API (/api/proposals/*)
data_sources/           # (Faz 3) Sightline reliefweb_api uyumlu veri katmanı
ops/                    # (Faz 2) tracing + usage ledger (Waku deseni)
templates/ + static/    # SPA — Sightline Liquid Glass tasarımı (Faz 1)
tests/                  # pytest
docs/ARCHITECTURE.md    # Mimari ilkeler
```
