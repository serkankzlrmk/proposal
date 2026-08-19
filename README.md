# Proposal Studio

Sightline projesinin **hibe teklifi (grant proposal) üretim pipeline'ı**. Bir donor
çağrısını (call) okuyup, o çağrının kurallarına göre insani yardım proposal'ı üretir;
her adımda insan onayı ve düzenleme hakkı korunur.

> Sightline reposundan bağımsız geliştirilir; entegrasyon notu:
> `docs/EVIDENCE_BRIDGE.md`.

---

## Ne Yapar

Uçtan uca akış:

```
Donor call belgeleri (pdf/docx/md)
        │
        ▼
Call Ingestion ──► özet + gereklilikler çıkarılır ──► insan onayı
        │                                                  │
        ▼                                                  ▼
  Proposal oluşturma ◄── donors/<call_id>.yaml (manifest, engine otomatik yükler)
        │
        ▼
  Adım 1  Context & Targeting      (AI taslak + elle düzenleme)
  Adım 2  Theory of Change         (AI üretim + elle node ekle/sil)
  Adım 3  4x4 Logframe             (AI üretim + elle satır ekle/sil, GOAL/OUTCOME/OUTPUT/ACTIVITY)
  Adım 4  Narrative / Risk / Bütçe (3 alt sekme, her birinde ayrı agent)
  Adım 5  Blind Verifier + PDF     (deterministik skor + Typst PDF)
```

Skorlama **deterministiktir** (LLM karar vermez): donor manifest'teki kurallara göre
5 kriter üzerinden hesaplanır ve hard eligibility ihlali otomatik ret üretir.

---

## Kurulum & Çalıştırma

```bash
# Bağımlılıklar (Python 3.11)
uv venv --python 3.11
uv pip install --python .venv/bin/python -r requirements.txt

# Sunucu
PYTHONPATH="" VIRTUAL_ENV=$(pwd)/.venv .venv/bin/python app.py
# → http://127.0.0.1:5002

# Test
.venv/bin/python -m pytest tests/ -q
```

Gereksinimler: `flask`, `flask-cors`, `httpx`, `python-dotenv`, `typst`, `pymupdf`,
`requests`, `langchain`, `pypdf`.

---

## Proje Yapısı

```
app.py                      Flask sunucusu (:5002) + blueprint kayıtları
config.py                   Ortam ayarları (port, LLM endpoint, key'ler)
db.py                       SQLite katmanı: proposal CRUD, step kilidi (FSM),
                            audit log (proposal_reviews), call draft tablosu

blueprints/
  proposal_api.py           Proposal CRUD, AI üretim uçları, full-summary,
                            PDF export (eligibility kapısı), advisor chat
  call_ingest_api.py        Call yükleme (çoklu dosya), draft onay/red, brief
  step3_logframe.py         Logframe analyze (SMART), lock, generate
  step4_budget_risk.py      Risk matrisi / bütçe analizi, lock, alt-sekme agent'ları

engine/
  models.py                 Pydantic şemaları: DonorManifest, LogframeIndicator,
                            RiskMatrixItem, BudgetItem, PseaCommitments
  yaml_rules.py             Donor manifest yükleyici + deterministik skor motoru
                            (5 kriter, hard gates, lineer bütçe cezası)
  donor_resolver.py         Donor id çözümleme (call-ingested ↔ builtin)
  donor_rules.py            Eski Python donor profilleri (geri uyumluluk)
  call_ingest.py            Call belge → gereklilik çıkarımı, anti-halüsinasyon
                            gate doğrulama, manifest üretimi
  generator.py              ToC / Logframe / Narrative üretimi (manifest-aware),
                            yapısal logframe → matrix projeksiyonu
  smart_parser.py           SMART gösterge doğrulama + deterministik güçlendirme
  advisor.py                Etkileşimli danışman (small-talk hızlı yolu + LLM)
  advisor_context.py        Danışman bağlam şeması (superset)
  verifier.py               Blind verifier (LLM-as-a-judge, ayrı model)
  evidence.py               Sightline köprüsü: ReliefWeb/HDX tool'larını
                            kod kopyalamadan çalışma zamanında kullanır

typst_engine/compiler.py    Typst PDF üretimi (narrative, logframe, risk,
                            bütçe, gerçek skor bloğu)
donors/*.yaml               Donor manifestleri (OCHA, USAID, EU, Generic +
                            call-ingested olanlar)
ops/tracing.py              LLM kullanım ledger'ı (JSONL: token, maliyet, süre)
templates/ + static/        SPA (6 adımlı wizard + landing + donor call bölümü)
docs/
  EVIDENCE_BRIDGE.md        Sightline entegrasyon/taşıma notu
  SYSTEM_DESIGN.md          Sistem tasarımı
  ARCHITECTURE.md           Mimari ilkeler
  BACKEND_DESIGN.md         Backend tasarım notları
```

---

## Donor Manifest Sistemi

Her donor, kök seviyede deklaratif bir YAML'dir; yeni donor eklemek = 1 dosya:

```yaml
donor_id: ocha_cbpf
display_name: OCHA Country-Based Pooled Funds
currency: USD
overhead_cap_percent: 7
mandatory_keywords: [PSEA, Sphere standards, protection mainstreaming]
hard_eligibility_gates:
  sadd_disaggregation_mandatory: true
mandatory_sections: [Executive Summary, Context, ...]
```

Skor motoru (`engine/yaml_rules.py`) bu manifest'i yükler:

- **5 kriter** — section_coverage (30), source_citations (25), smart_criteria (20),
  donor_keywords (15), budget_alignment (10) — toplam 100
- **Hard gates** — ihlal edilen kota/koşul varsa skor ne olursa olsun
  `AUTOMATIC_REJECTION`; PDF export 403 ile kilitlenir
- **Lineer bütçe cezası** — overhead cap'i aşan kısım için `10 − (aşım × 5)`
- **Zero-crash** — eksik/bozuk kural asla patlamaz; 0 puan + `WARNING_MISSING_RULE`

## Call Ingestion (İnsan-Onaylı Kural Çıkarımı)

1. **Yükleme** — pdf/docx/md, **çoklu dosya** (guidelines + form + annex tek seferde)
2. **Çıkarım** — özet + gereklilikler + deadline + bütçe kuralı (TRY/tavan) + hard gates
3. **Anti-halüsinasyon** — LLM'in iddia ettiği her gate, belge metninde **kanıtlanmak
   zorundadır**; kanıt yoksa manifest'e girmez (deterministik doğrulama)
4. **İnsan onayı** — brief ("ne diyor, ne istiyor, ne yapılmalı") + Publish/Reject
5. **Manifest** — `donors/<call_id>.yaml` yazılır, engine bir sonraki istekte otomatik
   kullanır (glob tabanlı dinamik yükleme)

## Sightline Evidence Köprüsü

`engine/evidence.py` Sightline'ın `reliefweb_api/` tool'larını (ReliefWeb sitrep
arama, HDX ülke/mülteci/IDP verisi) **kod kopyalamadan** çalışma zamanında çağırır:

- Sightline root'u `sys.path`'e eklenir, modüller paket olarak import edilir
- HDX key'i Sightline'ın kendi `.env`'inden okunur (bu repoya kopyalanmaz)
- Toplanan kanıt `[ref: SIGHTLINE_*]` alıntılarıyla prompt'a girer; alıntılar
  citation registry'de grounded sayılır (source_citations skoru)
- Sightline yoksa/kapalıysa tüm çağrılar `None` döner — pipeline asla kırılmaz

Taşıma anında yapılacaklar: `docs/EVIDENCE_BRIDGE.md` (checklist).

---

## Frontend

Tek sayfa (SPA): landing (proposal listesi + silme) → "+ New" pop-up
(published call / hazır donor / yeni call yükleme) → 5 adımlı wizard + sonda
ayrı Donor Call bölümü. Sağ altta 💬 floating danışman (tıklayınca pop-up).
Her adımda AI üretimi ve elle düzenleme birlikte çalışır; alt sekmelerin
(Risk, Bütçe) kendi agent butonları vardır.

## LLM Kullanımı (sadece gereken yerlerde)

- Call gereklilik çıkarımı ve brief üretimi
- ToC / Logframe / Narrative taslak üretimi
- Risk / Bütçe taslak üretimi (alt-sekme agent'ları)
- Blind verifier (ayrı model, düşünce zinciri verilmez)
- Danışman sohbet

Tüm LLM çağrıları `ops/usage.jsonl`'e yazılır (token, maliyet, gecikme). Üretim
hattının **kritik kararları** (skor, eligibility, gate doğrulama) her zaman
deterministik koddadır.

## Test Kapsamı

| Dosya | Konu |
|---|---|
| `test_yaml_rules.py` | Manifest yükleme, skorlama, hard gates, bütçe cezası |
| `test_call_ingest.py` | Çoklu format çıkarım, anti-halüsinasyon, brief, API akışı |
| `test_step3_logframe.py` | Yapısal logframe, SMART parser, lock (FSM) |
| `test_proposal.py` | Uçtan uca akış, blind verifier, PDF |
