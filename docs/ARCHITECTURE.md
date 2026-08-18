# Proposal — Mimari İlkeler (Architecture Principles)

> Bu doküman proposal sisteminin geliştirme kurallarını tanımlar.
> **Kritik kural: proposal, Sightline'dan bağımsız bir projedir.**

## 1. Bağımsızlık (İlk Kural)

- Proposal, Sightline (RedAgent) **kodunu import etmez, kopyalamaz, değiştirmez.**
- Sightline deposuna bu projeden **hiçbir dosya yazılmaz.**
- Sightline'ın testleri, deploy'u, veritabanları bu projeden **etkilenmez.**
- Geliştirme aşamaları (branches, PR'lar, commit'ler) **tamamen ayrı** yürür.

## 2. Sightline ile Uyumluluk (İleride Entegrasyon İçin)

Sistem ileride Sightline'a **ayrı modül olarak** eklenecektir. Bu yüzden:

- Veri kaynağı katmanı (`data_sources/`), Sightline'ın `reliefweb_api/` deseniyle
  **birebir uyumlu** tasarlanır:
  - Her kaynak: `<name>_client.py` (singleton HTTP client, SimpleCache, rate limit, timeout)
  - Her kaynak: `<name>_tools.py` (fonksiyonlar)
  - Ortak desenler `reliefweb_config.py` benzeri tek bir yerde
- Endpoint isimleri ve veri yapıları, Sightline'a taşınırken değişiklik gerektirmeyecek
  şekilde seçilir.
- **Taşıma anında**: proposal'daki `data_sources/` modülü, Sightline'ın `reliefweb_api/`
  klasörüne bütün olarak taşınabilir olmalı (import path'leri göreli kalır).

## 3. UI Dili

- UI'da **TÜRKÇE metin YOK** (placeholder, hata mesajı, badge dahil — her şey İngilizce).
- Tasarım dili: Sightline'ın Apple Liquid Glass sistemi (ileride birebir eşleşecek).

## 4. Gözlemlenebilirlik (LLM-Ops)

- Her AI aksiyonu (generate, verify, advisor, export) bir trace event'i üretir.
- Token kullanımı kalıcı `usage.jsonl` ledger'ına yazılır (tokens = ground truth).
- Bu katman Waku'nun `ops/tracing.py` deseniyle uyumludur.

## 5. Kalite Standartları

- Her değişiklik testlerle gelir: `pytest tests/ -v` hepsi yeşil olmadan commit yok.
- Commit mesajları: konu + neden (WHY), 70 karakter altı.
- Büyük refactor/kaldırma işleri ayrı branch'te yürür, kullanıcı onayı olmadan
  main'e push yok.
