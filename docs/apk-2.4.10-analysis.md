# Ekşi Sözlük Android 2.4.10 API analizi

Bu belge `other/ekşi+sözlük_2.4.10.apk` dosyasının statik analiz kaydıdır. APK
SHA-256 değeri `5216b33c593f0acad2af1f7eaf0fedbbca8d8605eec2d8cfffa103967fc6187f`,
`versionName=2.4.10`, `versionCode=144`, target/compile SDK 36 ve analiz aracı
JADX 1.5.5'tir. Tekrar üretmek için:

```console
python scripts/analyze_apk.py other/ekşi+sözlük_2.4.10.apk
```

JADX bazı üçüncü taraf/obfuscation sınıflarında 97 decompile uyarısı verdi. Retrofit
arayüzü, istek modelleri ve aşağıdaki çağrı noktaları okunabildi; uyarılar bu
bulguları etkilemedi. Dinamik trafik gözlemi hesapta gerçek değişiklik yaratacağı
için otomatik yapılmadı. `tests/test_live_contract.py` yalnız açık opt-in ile güvenli
okuma sözleşmesini sınar.

## Retrofit eşlemesi

| Obfuscated annotation | Anlamı |
|---|---|
| `InterfaceC3267zw` | GET |
| `InterfaceC2918uO` | POST |
| `InterfaceC0370Fs` | form-urlencoded |
| `InterfaceC0494Hq` | field map |
| `InterfaceC0368Fq` | field |
| `InterfaceC1646a9` | JSON body |
| `InterfaceC0909OR` | query |
| `InterfaceC2982vP` | path |

Ana Retrofit arayüzü `sources/p000_/InterfaceC1999fn.java` içinde 100'den fazla
deklarasyon içeriyor. Araştırma kapsamındaki doğrulanmış uçlar aşağıdadır.

## Hesap eylemleri ve risk sınıfları

| İşlem | HTTP uç / gövde | İstemci metodu | MCP riski |
|---|---|---|---|
| entry oluştur | `POST v2/entry/add`, `Title`, `Content` | `create_entry` | kritik, yayın öncesi insan onayı |
| entry düzenle | `POST v2/entry/edit`, `Title=""`, `Id`, `Content` | `edit_entry` | kritik/destructive |
| entry sil | `POST v2/entry/delete`, `Id` | `delete_entry` | kritik/destructive |
| favori / kaldır | `POST /v2/entry/favorite|unfavorite`, `Id` | `favorite_entry`, `unfavorite_entry` | orta |
| oy / oyu kaldır | `POST v2/entry/vote`, `Id`, `Rate=1|-1`; `vote/remove` | `vote_entry`, `remove_entry_vote` | orta |
| başlık takip / bırak | `POST v2/topic/follow|unfollow`, `Id` | `follow_topic`, `unfollow_topic` | orta |
| kullanıcı engelle / aç | `POST v2/user/block|unblock`, `nick` | `block_user`, `unblock_user` | yüksek |
| mesaj gönder | `POST v2/message/sendmessage`, JSON `message,to,threadId` | `send_message` | kritik, dış iletişim |
| mesaj dizisi | `GET v2/message/thread/Nick/{username}?p=` | `message_thread` | hassas okuma |
| arşiv mesaj dizisi | `GET v2/message/archivethread/Nick/{username}` | `archived_message_thread` | hassas okuma |
| okundu işaretle | `POST v2/message/markread/nick`, `nick` | `mark_message_thread_read` | düşük yazma |
| taslak kaydet / sil | `POST v2/topic/savedraft|deletedraft`, `Title`, opsiyonel `Content` | `save_draft`, `delete_draft` | orta/yüksek |
| düzenlenebilir içerik | `GET v2/entry/edit/{entryId}` | `editable_entry` | hassas okuma |

Ek olarak APK; `settings/get|set/personal`, `settings/get|set/preferences`, çöp
kutusu liste/sil/boşalt/dirilt, sabitlenmiş entry, index-title block, push tercihleri,
mesaj arşivleme/silme batch modelleri, profil ve MFA uçlarını içeriyor. Bunlar
OpenAPI envanterinde belgelenmiştir; hesap kapatma, ödeme, MFA ve profil gizliliği
uçları MCP'ye kasıtlı olarak açılmamıştır.

## Mesaj batch modelleri

- `v2/message/deleteprocessthread`: JSON `ThreadIdList` öğeleri
  `{ThreadId, MaxMessageId}`.
- `v2/message/archiveprocessthread`: aynı thread tanımlayıcı modeli.
- `v2/message/deleteprocessarchive`: JSON `ArchiveIdList` öğeleri `{ArchiveId}`.

## Güvenlik kararı

Python istemcisindeki bütün yazmalar `dry_run=True` ile deterministik bir
`WritePreview` üretir, otomatik retry kullanmaz ve kimlik bilgisi içermeyen audit
olayı bırakır. MCP varsayılan olarak salt okunurdur. Interactive modda preview
token'ı istek alanlarını bağlar; publish aşamasında MCP istemcisinin insan
elicitation callback'i onay vermeden eylem yürütülmez. Modelin gönderdiği bir
`confirm=true` alanı onay yerine geçmez.
