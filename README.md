# P2PChat

Ứng dụng chat P2P (peer-to-peer) không cần server, có mã hóa và giao diện GUI. Project môn Lập trình Mạng — UTH.

---

## Thông tin nhóm

| Thành phần | Thông tin |
| --- | --- |
| Lớp | 012012301310 |
| Nhóm | Group 04 — UDM09 |
| Ngôn ngữ | Python 3.13+ |
| Mô hình | Peer-to-Peer, không server trung tâm |
| Nền tảng | Windows |
| Repo | [012012301310\_Group04\_UDM09](https://github.com/tranhuultai/012012301310_Group04_UDM09) |

### Thành viên

| MSSV | Họ tên |
| --- | --- |
| 089205009200 | Trần Hữu Tài |
| 052206013184 | Nguyễn Văn Tài |
| 080306012851 | Trần Thị Thanh Thơ |
| 052206003938 | Nguyễn Phan Hoài Bin |
| 082206002652 | Lê Quốc Thịnh |

---

## Mô tả

Các máy tính trong cùng mạng LAN tự tìm thấy nhau qua UDP broadcast, sau đó kết nối trực tiếp qua TCP để chat. Không có server trung tâm — mỗi máy vừa là client vừa là server.

Tin nhắn được mã hóa end-to-end: RSA-2048 để trao đổi khóa lúc kết nối, Fernet (AES-128) để mã hóa nội dung trong suốt phiên chat. Có thể gửi file tối đa 10MB, kiểm tra toàn vẹn bằng SHA-256.

---

## Chức năng

### Networking

- Tự động tìm peer trong LAN qua UDP broadcast (mỗi 5 giây)
- Kết nối TCP trực tiếp, không relay
- Kết nối thủ công bằng cách nhập IP:Port
- Mỗi kết nối chạy trên thread riêng, GUI không bị đơ

### Bảo mật

- RSA-2048: trao đổi khóa khi bắt đầu kết nối
- Fernet: mã hóa toàn bộ tin nhắn và file trong session
- JWT: xác thực gói UDP discovery, chống giả mạo
- TOFU (Trust On First Use): lưu fingerprint RSA của peer, cảnh báo nếu key thay đổi
- Chống replay attack: `deque + set` track message_id đã thấy, cộng thêm kiểm tra tuổi tin nhắn (>5 phút bị drop) để không phụ thuộc hoàn toàn vào kích thước cố định của deque

### File transfer

- Hỗ trợ PDF, DOCX, TXT, PNG, JPG, ZIP (giới hạn 10MB)
- Mã hóa file bằng Fernet, encode Base64 để truyền qua JSON
- Verify SHA-256 sau khi nhận xong
- File lưu vào `src/downloads/`

### Giao diện

- Sidebar tự cập nhật danh sách peer, hiển thị tin nhắn gần nhất
- Chat bubble trái/phải, có timestamp
- File bubble hiện trong khung chat (không cần mở tab khác)
- Lịch sử chat được lưu và tải lại khi mở app

---

## Cài đặt

```bash
pip install -r requirements.txt
```

Chỉ cần chạy app thì tối thiểu là:

```bash
pip install customtkinter cryptography PyJWT pywinstyles
```

`pywinstyles` chỉ dùng cho một fix cosmetic trên Windows (chống chớp màn hình đen khi resize cửa sổ) — thiếu nó app vẫn chạy bình thường, chỉ mất hiệu ứng đó.

Yêu cầu Python 3.13 trở lên, Windows 10/11.

---

## Chạy

```bash
cd Code/P2PChat/src

# Chạy bình thường (port 12000)
python main.py

# Chạy thêm instance để test trên cùng máy
python main.py 1000
```

---

## Hướng dẫn dùng

**Kết nối với peer:**

Nếu cùng mạng LAN thì peer tự xuất hiện trong sidebar sau vài giây, click vào rồi nhấn Connect là xong.

Nếu khác mạng thì nhấn "+ Add / Discover Peer" ở dưới sidebar, nhập IP và Port rồi Connect.

**Gửi file:**

Chọn peer đã connect → nhấn "Send File" ở panel bên phải → chọn file → bên nhận sẽ thấy thông báo trong chat, nhấn Download để nhận.

**TOFU — xác minh danh tính:**

Lần đầu gặp peer mới sẽ có hộp thoại hỏi có trust không. Nếu fingerprint của peer thay đổi so với lần trước thì app cảnh báo (có thể là MITM), user tự quyết định chấp nhận hay block.

---

## Kiến trúc

```
GUI (CustomTkinter)
  └── ChatApp / MainWindow / Sidebar / ChatBox / PeerDetails
        │
        │  (callbacks)
        ▼
  ChatController  ←→  P2PNode (TCP server, handshake, sessions)
                  ←→  Discovery (UDP broadcast)
                  ←→  TransferManager (file state machine)
                        │
                   Security & Storage
                   RSA · Fernet · JWT · TOFU · MessageHistory
```

**TCP framing:** TCP là stream, không có ranh giới message. Giải pháp: 4 byte đầu mỗi message chứa độ dài payload (big-endian uint32), phần sau là JSON.

**Handshake 3 bước:**

1. Peer A (người bấm Connect) gửi `HANDSHAKE` kèm public key của A.
2. Peer B nhận, trả `HANDSHAKE_ACK` kèm public key của B. ACK này **chưa có** session key.
3. Peer A nhận ACK, tự tạo session key (Fernet), mã hóa bằng public key của B, gửi trong gói `SESSION_KEY`. Peer B giải mã bằng private key của B rồi kích hoạt session — không có bước ACK ngược lại, session coi như sẵn sàng ngay khi B giải mã thành công.

Nói cách khác: **bên chủ động Connect (A) luôn là bên tạo và gửi session key**, không phải bên nhận kết nối. Từ đó hai bên dùng session key (Fernet) để mã hóa mọi thứ.

**UDP Discovery:** Mỗi 5 giây broadcast một gói JSON lên port 15000, gồm `peer_id`, `username`, `fingerprint`, `public_key`, `port` ở dạng plaintext, cộng thêm một trường `identity_token` — đây mới là JWT (RS256, tự ký bằng private key của chính peer đó), chỉ chứa claim `peer_id`, `username`, `fingerprint`. Peer nhận verify chữ ký JWT bằng public key đi kèm trong cùng gói để xác nhận danh tính khớp với key, rồi mới cập nhật danh sách. Lưu ý: `port` không nằm trong JWT nên không được ký — đây là giới hạn đã biết của thiết kế hiện tại, không phải lỗi cần sửa trong Sprint 4.

**File transfer:**

```
Sender gửi FILE_META
  → Receiver thấy, user click Download
  → Receiver gửi DOWNLOAD_REQUEST
  → Sender gửi FILE_START, rồi FILE_CHUNK liên tục, cuối là FILE_COMPLETE + sha256
  → Receiver verify sha256, đổi tên file .part thành tên thật
```

---

## Cấu trúc thư mục

```
src/
├── main.py
├── config.py                  # tất cả constants
├── gui/
│   ├── app.py                 # root, wire callbacks
│   ├── main_window.py         # layout 3 cột
│   ├── sidebar.py
│   ├── chatbox.py
│   ├── chat_bubble.py
│   ├── peer_details.py
│   ├── transfer_panel.py
│   ├── trust_dialog.py        # TOFU dialog
│   ├── statusbar.py
│   ├── theme.py               # màu sắc, fonts
│   ├── ui_state.py
│   └── win_compat.py          # Windows-only cosmetic fix, no-op nếu thiếu pywinstyles
├── network/
│   ├── node.py                # TCP server + handshake (~1180 dòng, intentional — session/handshake state machine, không tách nhỏ)
│   ├── discovery.py
│   ├── transfer_manager.py
│   └── validation.py
├── controllers/
│   └── controller.py          # adapter GUI ↔ Node
├── message/
│   └── protocol.py            # 4-byte framing + 14 packet types
├── identity/
│   └── identity_manager.py    # RSA keypair, peer_id = SHA256(pubkey)
├── trust/
│   ├── tofu_engine.py         # NEW→TRUSTED/VERIFIED/MISMATCH/BLOCKED
│   └── trust_store.py
├── security/
│   ├── rsa_utils.py
│   ├── crypto.py              # Fernet session encryption
│   └── jwt_handler.py
├── storage/
│   ├── contact_book.py
│   ├── message_history.py     # thread-safe với Lock
│   └── storage_manager.py     # atomic write
├── downloads/
└── test/                      # 200 unit tests
```

---

## Test

```bash
cd Code/P2PChat/src
python -m pytest test/ --ignore=test/test_statusbar.py -q
# 214 passed
```

`test_statusbar.py` bị `--ignore` vì nó tạo một cửa sổ Tk thật (`ctk.CTk()`) để test — máy không có display (SSH/CI không có Xvfb) sẽ crash ngay ở bước tạo cửa sổ. Chạy riêng file đó (`pytest test/test_statusbar.py`) vẫn được nếu máy có display.

## UI thread-safety guarantee

`P2PNode` chạy handshake/discovery/nhận tin nhắn trên các background thread riêng (accept loop, mỗi peer 1 receive thread, discovery listener, cleanup timer), nhưng **không có nơi nào trong số đó được phép đụng trực tiếp vào widget Tk** — CustomTkinter/Tkinter chỉ an toàn khi thao tác từ main thread.

Ranh giới an toàn nằm ở `gui/app.py` và `ChatController(schedule_gui=...)`:

- `ChatApp.__init__` truyền `schedule_gui = lambda fn: self.after(0, fn)` xuống `ChatController` → `TransferManager`, nên mọi callback file-transfer (`on_transfer_started/progress/complete`, `on_file_meta`) đã chạy trên main thread **trước khi** tới `gui/app.py`.
- Các callback còn lại (`on_connected`, `on_disconnect`, `on_peer_discovered`) tự bọc phần đụng tới `ui_state`/widget trong một closure và gọi `self.after(0, closure)` ngay tại `gui/app.py` — xem `_on_connected`, `_on_disconnect`, `_on_peer_discovered`.
- `on_message` không có phần GUI đồng bộ: nó chỉ enqueue vào một `deque` thread-safe rồi lên lịch `self.after(0, self._drain_messages)` — việc dựng bubble tin nhắn thật sự luôn chạy trong `_drain_messages` trên main thread.

Quy tắc khi thêm callback mới từ `network/`: không bao giờ gọi thẳng vào `self.main_window`/`self.ui_state`/bất kỳ widget nào trong hàm được `P2PNode`/`TransferManager` gọi trực tiếp — luôn bọc trong `self.after(0, ...)` (hoặc để `TransferManager` làm việc đó qua `schedule_gui`) trước khi chạm GUI.

## Giới hạn bảo mật đã biết (không phải bug — trade-off có chủ đích)

- **Không có forward secrecy:** session key (Fernet) được sinh mới mỗi lần kết nối, nhưng truyền đi bằng cách mã hoá RSA với public key *dài hạn* của peer nhận (xem `_send_session_key` trong `node.py`). Nếu private key dài hạn của một bên từng bị lộ trong tương lai, kẻ tấn công lưu lại lưu lượng RSA-wrapped session-key trước đó *về lý thuyết* có thể giải mã lại các phiên chat cũ. Khắc phục đúng cách cần đổi sang trao đổi khoá ephemeral (ECDHE/X25519) — đây là thay đổi giao thức lớn (thêm bước handshake, đổi format gói tin, ảnh hưởng toàn bộ test khởi tạo session), không phải một bug-fix nhỏ, nên chưa thực hiện trong đợt rà soát này.
- **TOFU không xác thực lần gặp đầu tiên:** giống mọi mô hình trust-on-first-use (kể cả SSH known_hosts), nếu chính kết nối *đầu tiên* với một peer bị MITM, TOFU không có cách nào phát hiện — nó chỉ phát hiện được từ lần gặp **thứ hai** trở đi khi fingerprint đổi khác (trạng thái `MISMATCH`). Đây là hạn chế cố hữu của TOFU, được chọn có chủ đích thay vì một hệ thống CA tập trung (không phù hợp với một ứng dụng P2P LAN đơn giản).
