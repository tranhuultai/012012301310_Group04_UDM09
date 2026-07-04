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
| 052206003938 | Nguyễn Phan Hoài Bình |
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
# 200 passed
```

`test_statusbar.py` bị `--ignore` vì nó tạo một cửa sổ Tk thật (`ctk.CTk()`) để test — máy không có display (SSH/CI không có Xvfb) sẽ crash ngay ở bước tạo cửa sổ. Chạy riêng file đó (`pytest test/test_statusbar.py`) vẫn được nếu máy có display.