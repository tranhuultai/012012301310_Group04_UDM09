# 💬 UDM_09 · P2P Chat GUI

> Ứng dụng chat ngang hàng (Peer-to-Peer) không cần server trung tâm.
> Project được phát triển cho môn Lập trình Mạng bằng Python.

---

## 📌 Thông tin project

| Thành phần | Thông tin                   |
| ---------- | --------------------------- |
| Lớp        | 012012301310                |
| Nhóm       | Net2_Group_04               |
| Ngôn ngữ   | Python 3.13+                |
| Mô hình    | Peer-to-Peer (P2P)          |
| Nền tảng   | Windows Desktop Application |
| IDE        | Visual Studio Code          |
---

## 📖 Tổng quan

P2P Chat GUI là project xây dựng ứng dụng chat ngang hàng có giao diện GUI chạy trên Windows.

Ứng dụng cho phép các peer kết nối trực tiếp với nhau thông qua TCP Socket mà không cần sử dụng server trung tâm.

Project tập trung vào:

* TCP Socket Programming
* Peer-to-Peer Communication
* Multi-threading
* GUI Desktop Application
* Message Encryption

---

## ✨ Chức năng dự kiến

### Chức năng cốt lõi

* Kết nối trực tiếp giữa các peer
* Chat realtime
* Broadcast message
* Mã hóa tin nhắn bằng Fernet (AES-128-CBC)
* Multi-thread networking
* Theo dõi trạng thái kết nối
* Validate IP và port đầu vào

---

### Chức năng mở rộng

* Logging lịch sử chat
* Reconnect peer
* File transfer cơ bản
* Theo dõi hiệu suất hệ thống
* Stress test và performance test

---

## ⚙️ Kiến trúc kỹ thuật dự kiến

### Giao thức truyền dữ liệu

```text
┌─────────────┬──────────────────────────────┐
│  4 bytes    │  N bytes                     │
│  (length)   │  (encrypted payload)         │
└─────────────┴──────────────────────────────┘
```

* 4 bytes đầu dùng để xác định kích thước dữ liệu
* Payload sẽ được mã hóa trước khi truyền

---

### Mô hình threading

```text
main thread (GUI)
    │
    ├── server-listener thread
    ├── connect thread
    └── receive thread × N
```

---

## 🏗️ Cấu trúc project

```text
UDM09_P2PChat-GUI-
├── Code/
│   └── P2PChat/
│       └── src/
│           ├── main.py
│           ├── protocol.py
│           ├── gui/
│           │   └── app.py
│           └── node/
│               └── core.py
│
├── DOCX/
├── PPTX/
├── Extra/
│
├── .gitignore
├── README.md
└── requirements.txt
```

---

## 🚀 Khởi tạo project

```bash
git clone https://github.com/tranhuultai/UDM09_P2PChat-GUI-.git
```

---

## 📋 Tiến độ Sprint

### ✅ Sprint 1 (Tuần 1–2)

* [x] Nghiên cứu mô hình P2P và cơ chế AES-128-CBC
* [x] Setup GitHub repository
* [x] Thiết kế cấu trúc project và giao diện GUI
* [x] Implement TCP connection
* [x] Implement handshake cơ bản

---

### 🔄 Sprint 2 (Tuần 3–4)

* [ ] Implement chat realtime
* [ ] Implement broadcast messaging
* [ ] Tích hợp trao đổi key và mã hóa Fernet vào luồng gửi/nhận
* [ ] Multi-thread networking
* [ ] Xử lý exception và kết nối

---

### ⏳ Sprint 3 (Tuần 5–6)

* [ ] Hoàn thiện GUI
* [ ] Theo dõi trạng thái và thống kê peer
* [ ] Logging lịch sử chat
* [ ] Thử nghiệm nhiều peer kết nối

---

### ⏳ Sprint 4 (Tuần 7–8)

* [ ] Tối ưu hiệu suất
* [ ] Stress test và performance test
* [ ] Hoàn thiện tài liệu
* [ ] Fix bug lần cuối

---

## 📦 Requirements

```txt
Python 3.13+
cryptography
customtkinter
```

---

## 👥 Thành viên nhóm

| MSSV         | Họ tên               |
| ------------ | -------------------- |
| 089205009200 | Trần Hữu Tài         |
| 052206013184 | Nguyễn Văn Tài       |
| 080306012851 | Trần Thị Thanh Thơ   |
| 052206003938 | Nguyễn Phan Hoài Bin |
| 082206002652 | Lê Quốc Thịnh        |
| 064206008244 | Nguyễn Xuân Thủy     |

---

## 📄 Ghi chú

> Project hiện đang trong giai đoạn khởi tạo và thiết kế hệ thống.
> Các chức năng sẽ được triển khai theo từng sprint.



---
