# Proposal Studio — Sistem Dizaynı v4 (NotebookLM Spec Entegre) (18 Ağu 2026)

> **STATUS: DİZAYN — aksiyon yok.** `.hermes/plans/` gitignored.
> v4: NotebookLM'den gelen teknik spesifikasyon (diğer agent üzerinden)
> dizayna entegre edildi. Uyumluluk teyit edildi — iki kaynak aynı mimariyi
> söylüyor: YAML-driven kurallar + deterministik puanlama + interaktif trace.

---

## 0. Teyit: NotebookLM Spec ↔ v3 Dizayn Uyumu

| NotebookLM Spesifikasyonu | v3 Dizayn | Uyum |
|---------------------------|-----------|------|
| `/donors/<donor_id>.yaml` manifest | `rules/donor_*.yaml` | ✅ aynı konsept |
| `YamlDonorRuleLoader` + schema validation | `manifest_loader.py` | ✅ |
| 5 puanlama kriteri (30/25/20/15/10) | aynı 5 kriter + ağırlıklar | ✅ birebir |
| `evaluate_rule_safely` → 0 puan + WARNING_MISSING_RULE | "tanımsız → 0 + uyarı" | ✅ |
| Trace JSON (criterion/score/target_step/target_field) | `trace.py` | ✅ (spec daha net) |
| Click → Scroll → Advisor → Apply → Re-Score | interaktif puanlama | ✅ aynı loop |

**Sonuç:** İki bağımsız kaynak aynı mimariyi üretti — bu dizayn sağlam.

---

## 1. YAML Donor Manifest (NotebookLM spec — canonical)

```yaml
# donors/custom_donor.yaml
donor_id: "custom_donor"
name: "Custom Institutional Donor"
version: "1.0.0"

scoring_weights:
  section_coverage: 30
  source_citations: 25
  smart_criteria: 20
  donor_keywords: 15
  budget_alignment: 10

rules:
  sections:
    mandatory:
      - "humanitarian_context"
      - "needs_assessment"
      - "strategic_justification"
      - "logframe"
      - "budget_breakdown"

  citations:
    min_source_ratio: 0.75   # citations / paragraph oranı

  smart_indicators:
    required_dimensions: [specific, measurable, achievable, relevant, time_bound]

  keywords:
    expected_tokens: [protection, gender, disability, sustainability, local_partner]

  budget:
    max_overhead_percent: 7.0
    required_categories: [personnel, operational, overhead]
```

**Graceful Fallback (spesifikasyondan):**
```python
def evaluate_rule_safely(rule_function, default_weight=0):
    try:
        return rule_function()
    except KeyError:
        return {
            "score": 0.0,
            "max_score": default_weight,
            "status": "WARNING_MISSING_RULE",
            "message": "Rule definition missing in donor YAML; defaulting to 0 points."
        }
```
→ Eksik kural asla crash etmez; 0 puan + uyarı. Donor profilleri kademeli doldurulur.

---

## 2. Deterministik Puanlama Formülleri (NotebookLM spec)

| Kriter | Max | Formül | Örnek |
|--------|-----|--------|-------|
| section_coverage | 30 | (mevcut zorunlu bölüm / toplam) × 30 | 4/5 × 30 = 24.0 |
| source_citations | 25 | min((geçerli alıntı / paragraf) × 25, 25) | 7/9 × 25 = 19.4 |
| smart_criteria | 20 | (geçen SMART boyut / toplam) × 20 | 3/5 × 20 = 12.0 |
| donor_keywords | 15 | (eşleşen anahtar / beklenen) × 15 | 2.5/5 × 15 = 7.5 |
| budget_alignment | 10 | 10 × (1 − max(0, (gerçek overhead% − cap%) / cap%)) | 10 × (1−0.17) = 8.3 |

Not: `source_citations` formülü spec'te **basit oran** (citations/paragraphs × 25) —
v3'teki karmaşık formül yerine. `budget_alignment` overhead yüzdesi üzerinden.

---

## 3. Trace Veri Yapısı (NotebookLM spec — canonical)

```json
{
  "setup_id": "setup_99812",
  "donor_id": "custom_donor",
  "total_score": 71.2,
  "trace": [
    {
      "criterion": "section_coverage",
      "score": 24.0,
      "max_score": 30,
      "target_step": "step2",
      "target_field": "humanitarian_context",
      "details": "4 out of 5 mandatory sections present. Missing: budget_breakdown."
    },
    {
      "criterion": "donor_keywords",
      "score": 7.5,
      "max_score": 15,
      "target_step": "step2",
      "target_field": "needs_assessment",
      "details": "Missing expected keywords: 'protection', 'disability'."
    }
  ]
}
```

**v3'e göre ek:** `target_step` + `target_field` — UI'da skor satırına tıklayınca
**tam olarak nereye** gidileceğini belirtir. Bu, interaktif loop'un kritik parçası.

---

## 4. Interaktif UI Loop (NotebookLM spec)

```
[ Score Table UI ]
  ├── section_coverage : 24.0 / 30 ──► (Click Row)
  ├── source_citations : 19.4 / 25          │
  └── donor_keywords   :  7.5 / 15 ◄────────┘
                               │
                               ▼
  [ Jump to Editor Field ] ──► (Focus: needs_assessment)
                               │
                               ▼
  [ AI Advisor Drawer ]  ──► "Add missing keywords: 'protection', 'disability'"
                               │
                               ▼
  [ Click "Apply" ]      ──► Text Updated ──► Auto Re-Score (7.5 ➔ 15.0)
```

Adımlar: Trace Row Click → Editor Auto-Scroll → AI Advisor Context Injection →
Apply & Re-Score Handshake. (v3'teki aynı akış, spec'le somutlaştı.)

---

## 5. Sightline Entegrasyonu (v3'ten korunan katman)

| Katman | Bağlantı | Değişiklik |
|--------|----------|------------|
| Backend | `blueprints/proposal_studio.py` | server.py: +2 satır |
| Frontend | `static/proposal-studio.js` | build: +1 satır |
| HTML | `#panel-proposal` placeholder | index.html: 1 blok |
| Kurallar | `donors/*.yaml` | **veri — kod değil** |
| Kaynaklar | `reliefweb_api/` + MCP (hazır) | import |
| LLM | `config.py` get_model() | import |
| Auth | `@require_auth` + `@require_role` | import |
| DB | `ps_setups`, `ps_traces` | izole |

Kaynak tool'ları (`sources.yaml`): search_sitreps, hdx_get_country_overview,
fts_get_plan_requirements, osm_query_nearby, brave_web_search, arxiv_search —
hepsi production'da canlı, import edilir.

---

## 6. Deliverables Checklist (kodlanacaksa — şimdi değil)

- [ ] `donors/` dizini + YAML manifest şablonları (ECHO dolu örnek, USAID/OCHA iskelet)
- [ ] `YamlDonorRuleLoader` (schema validation + fallback)
- [ ] 5 puanlama fonksiyonu (formüller spec'ten)
- [ ] `/analyze` endpoint'i trace log'ları response'a yazar
- [ ] Frontend skor tablosu → editor jump + Advisor Apply hook'ları
- [ ] pytest: kural motoru 0/1 testleri (deterministik)
- [ ] Waku mix: tracing (JSONL+SSE), retrieval_gate, consolidation

---

## 7. Açık Kararlar

1. **Manifest içeriği**: OCHA/USAID/EU kuralları `proposal/engine/donor_rules.py`'de
   (karakter limitleri, quota'lar) hazır — YAML'a taşınacak. Ben mi dönüştüreyim,
   sen mi YAML yazacaksın?
2. **Pass eşiği**: spec'te yok — 70/100 varsayılan (manifest'e eklenir).
3. **Kaynak tool'ları**: spec source_citations oranı kullanıyor (0.75) — Sightline
   tool'ları kaynak sağlayacak. Kullanıcı PDF/manuel kaynak eklemesi sonraki faz.
4. **NotebookLM rolü**: spec'te yok ama proposalfinder'da "grounded Q&A over PDFs"
   olarak kullanılmış — Proposal Studio'da advisor'ın kaynak tabanlı cevaplarında
   mı kullanılacak, yoksa sadece kuralların kaynağı mı?

---

*Dizayn v4 — NotebookLM spec entegre. Uyum teyit edildi. Aksiyon yok.*
