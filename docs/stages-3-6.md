# Aşama 3–6 uygulama matrisi

Bu belge roadmap maddelerini kod ve doğrulama kanıtlarına bağlar.

## Aşama 3 — sağlam Python SDK

| Gereksinim | Uygulama |
|---|---|
| async istemci | `eksiapi.async_client.AsyncEksiClient` |
| token expiry/refresh | `TokenInfo`, proaktif yenileme ve güvenli okumada tek 401 yenilemesi |
| retry/backoff | `RetryPolicy`, yalnız GET ve açıkça retryable read POST; yazmalarda tek deneme |
| rate limit | `RateLimitInfo`, `last_rate_limit`, `EksiRateLimitError.retry_after` |
| pagination | `Page`, sync/async topic iterator ve sync user iterator |
| typed/raw responses | `Entry`, `User`, `Message`, `ApiResponse`; `raw_response` seçeneği |
| ortak transport | `SyncTransport`/`AsyncTransport`, ortak response decoder |
| proxy/TLS/fingerprint | client constructor seçenekleri ve `AndroidFingerprint` |
| test transport/fixtures | `MockSession`, `AsyncMockSession`, sanitize APK fixture'ları |
| public mode | `EksiClient.anonymous()` / `AsyncEksiClient.anonymous()` |
| hatalar | status, request id ve güvenli response details taşıyan exception'lar |

## Aşama 4 — APK 2.4.10 keşfi

- `scripts/analyze_apk.py`, JADX çıktısından 159 Retrofit deklarasyonu çıkarır.
- `docs/apk-2.4.10-analysis.md`, APK hash/sürüm/build, annotation eşlemesi,
  request modelleri ve risk kararlarını kaydeder.
- `tests/fixtures/apk-2.4.10/`, entry, reaction, topic/user, mesaj ve taslak
  istek/yanıtlarını kimlik bilgisiz örneklerle sürümler.
- `openapi.yaml`, build 144 authentication/refresh ve araştırılan hesap uçlarını
  `x-eksi-risk` metadata'sıyla belgeler.
- `tests/test_live_contract.py`, yalnız `EKSI_LIVE_TESTS=1` ile açılır; üretimde
  yazma yapmaz. Gerçek yazma trafik testi güvenlik nedeniyle otomasyona alınmamıştır.

## Aşama 5 — Python yazma API'si

Sync ve async istemciler entry create/edit/delete, favorite, vote, topic/user
takibi, block/mute, mesaj, taslak, preferences, message batch ve trash eylemlerini
sağlar. Her eylem:

- input doğrular;
- `dry_run=True` ile `WritePreview` verir;
- idempotent/destructive bilgisini preview içinde taşır;
- otomatik retry kullanmaz;
- HTTP/envelope sonucunu doğrular;
- token/header içermeyen `AuditEvent` üretir.

## Aşama 6 — güvenli MCP yazmaları

- `eksi-mcp` varsayılan `readonly`, açık seçimle `--mode interactive` çalışır.
- Interactive modda entry publish/edit/delete, favorite, vote ve message için
  prepare/apply çiftleri bulunur.
- `PreviewStore` token'ları HMAC imzalı, süreli, işlem-bağlı ve tek kullanımlıdır.
- Apply araçlarının approval parametresi model-visible JSON Schema'dan çıkarılmış
  `Resolve` dependency'sidir; MCP istemcisinden `Elicit` ile insan onayı ister.
- Elicitation kabul edilse bile `confirm=false` hiçbir mutasyon yapmaz.

## Otomatik kalite kapıları

CI ve yerel doğrulama Python 3.10–3.14, Ruff, branch coverage (minimum %80),
OpenAPI validation, locked dependencies, wheel/sdist metadata ve temiz wheel
kurulumunu kapsar. Canlı kontrat testleri açık opt-in'dir.
