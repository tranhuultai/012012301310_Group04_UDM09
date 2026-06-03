# BÁO CÁO KIỂM THỬ HỆ THỐNG - SPRINT 2

* **Phân công nhiệm vụ:** Testing, Stress Test, Bug Tracking, Documentation.
* **Thư mục quản lý sản phẩm:** `DOCX/`, `Extra/`, `README.md`, `Code/P2PChat/tests/`.
* **Mục tiêu Sprint 2:** Xác thực tính ổn định của kiến trúc đa luồng (Multi-threading), cơ chế bảo mật trao đổi khóa phiên (Session Key Handshake), khả năng chịu tải (Stress Test) và trải nghiệm người dùng (UX) trên giao diện GUI CustomTkinter.

---

## I. PHÂN TÍCH LUỒNG HỆ THỐNG & CƠ CHẾ HOẠT ĐỘNG (ARCHITECTURE & FLOWS)

Hệ thống ứng dụng Chat P2P hoạt động dựa trên kiến trúc mạng ngang hàng, trong đó mỗi Peer đóng vai trò vừa là **Client** (chủ động kết nối đi) vừa là **Server** (mở cổng socket để lắng nghe kết nối đến).

### 1. Luồng Đa Luồng (Threading Flow)
Để đảm bảo giao diện đồ họa (GUI) không bị treo (Freeze) khi xử lý các tác vụ mạng, hệ thống phân tách thành các luồng độc lập:
* **Main Thread:** Chịu trách nhiệm render giao diện CustomTkinter và bắt các sự kiện từ người dùng (nhập text, bấm nút).
* **Listening Thread:** Chạy ngầm ngay sau khi người dùng bấm nút `Start`. Luồng này liên tục thực hiện hàm `socket.accept()` để chờ đón các kết nối đến từ Peer khác mà không làm block Main Thread.
* **Receive Thread (cho từng Peer):** Khi một kết nối được thiết lập, một luồng riêng biệt sẽ được cấp phát cho Peer đó để liên tục lắng nghe dữ liệu đến (`socket.recv()`), thực hiện giải mã và đẩy text lên màn hình.

### 2. Luồng Bắt Tay Bảo Mật & Đóng Gói Dữ Liệu (Packet & Handshake Flow)
Hệ thống sử dụng thư viện bảo mật `cryptography` để triển khai mã hóa đối xứng (Fernet/AES) kết hợp trao đổi khóa. Quy trình diễn ra theo 3 bước:
1. **TCP Connection:** Hai máy thiết lập kết nối Socket TCP thành công.
2. **Security Handshake:** Máy chủ động kết nối sẽ khởi tạo và gửi một Khóa phiên ngẫu nhiên (Session Key). Khóa này được truyền an toàn qua môi trường mạng bằng cách đóng gói theo cấu trúc packet quy định.
3. **Active Peer:** Máy nhận xử lý gói tin, lưu cấu trúc khóa ngầm (`Session key received`), xác thực danh tính đối phương (`Peer registered`) và chuyển trạng thái sang Ready để bắt đầu chuỗi chat mã hóa bảo mật.

---

## II. KỊCH BẢN VÀ KẾT QUẢ KIỂM THỬ THỰC TẾ (TEST CASES & EVIDENCE)

### 1. Kiểm thử chức năng cơ bản (Functional Testing)
* **Mục tiêu:** Xác thực luồng kết nối ban đầu, đồng bộ hóa danh sách (Peer Synchronization) và truyền tin thời gian thực.
* **Kịch bản kiểm thử:** Khởi chạy Máy A (`Listen port: 12000`) và Máy B (`Listen port: 12001`). Tiến hành điền IP `127.0.0.1` và Port `12001` từ Máy A để thực hiện kết nối, sau đó gửi tin nhắn thử nghiệm qua lại.
* **Kết quả thực tế:** * Hai máy thực hiện bắt tay TCP và trao đổi Session Key thành công.
  * Danh sách `Connected Peers` hiển thị chính xác IP/Port kết nối ngầm của đối phương.
  * Tính năng Broadcast gửi tin nhắn đồng bộ realtime, hiển thị chuẩn xác định dạng `<Tên_Peer> (broadcast): <Nội_dung>`.
* **Đánh giá:** **PASSED** (Đạt).

#### Hình ảnh bằng chứng:
![Giao diện kết nối và chat realtime 2 máy thành công](Extra/app_chay.png)

---

### 2. Kiểm thử tải và giới hạn hệ thống (Stress Testing)
* **Mục tiêu:** Xác thực độ ổn định của hệ thống đa luồng khi mở rộng quy mô mạng lưới (Scale) và tần suất truyền tin cực cao (Spam).
* **Kịch bản kiểm thử:**
  1. Mở đồng thời 4 Peer trên hệ thống từ cổng `12000` đến cổng `12003`.
  2. Thực hiện cấu hình kết nối chéo để tạo thành mạng lưới lưới (Mesh Network) ngầm.
  3. Thực hiện gửi liên tục (Spam Message) các chuỗi ký tự ngẫu nhiên với tần suất cao từ nhiều máy cùng một lúc.
* **Kết quả thực tế:** * Hệ thống sockets ngầm xử lý phân phối gói tin cực tốt. Tất cả các máy đều nhận đủ tin nhắn spam mà không bị rớt (drop) gói tin nào.
  * **Freeze Detection:** Giao diện CustomTkinter hoạt động mượt mà, khung chat tự động cuộn (Auto-scroll) theo dòng tin nhắn mới, không có hiện tượng giật lag hay treo ứng dụng (Freeze).
* **Đánh giá:** **PASSED** (Đạt).

#### Hình ảnh bằng chứng:
![Hệ thống chạy tải 4 peer đồng thời và spam tin nhắn liên tục](Extra/stress_test.png)

---

### 3. Khả năng chịu lỗi và Theo dõi lỗi (Bug Tracking & Fault Tolerance UX)

#### 🛠️ Bug logic phát hiện trên GUI (Đã xử lý chặn lỗi)
* **Mô tả lỗi:** Người dùng thao tác sai quy trình (Gõ tin nhắn vào ô chat hoặc bấm nút `Connect` khi chưa nhấn kích hoạt cổng lắng nghe ngầm).
* **Giải pháp xử lý (Fix Verification):** Hệ thống lập tức bắt sự kiện điều kiện, chặn đứng hành động gửi rác ra mạng và in ra dòng cảnh báo lỗi trực quan ngay trên khung chat: `[SYSTEM] Start the node first.` giúp định hướng người dùng thao tác đúng.

#### 🔌 Kịch bản kiểm thử lỗi: Ngắt kết nối đột ngột (Peer Disconnect)
* **Kịch bản:** Khi mạng lưới 4 Peer đang hoạt động ổn định, tiến hành tắt ngang cưỡng bức (Bấm nút X tắt cửa sổ) của máy `12002` để giả lập sự cố mất kết nối mạng đột ngột.
* **Kết quả thực tế xử lý lỗi ngầm:**
  * Hệ thống bắt được mã ngoại lệ mạng từ Windows: `[ERROR] Socket receive failed: [WinError 10054] An existing connection was forcibly closed by the remote host`.
  * Ứng dụng tự động giải phóng luồng xử lý của Peer đó, log thông báo `Peer disconnected: 127.0.0.1:12002 — remaining: 1`.
  * **GUI UX Update:** Giao diện các máy còn lại ngay lập tức cập nhật thời gian thực, xóa địa chỉ máy lỗi ra khỏi khung hiển thị `Connected Peers` và in log hệ thống báo `Connection lost` để người dùng nhận biết. Toàn bộ app không bị đứng hay crash theo.
* **Đánh giá:** **PASSED** (Đạt) - Hệ thống có khả năng chịu lỗi (Fault tolerance) rất cao, đạt chuẩn proof hoạt động ổn định.

#### Hình ảnh bằng chứng:
![Hệ thống bắt lỗi WinError 10054 và cập nhật giao diện khi tắt ngang peer](Extra/loi_tat_ngang.png)

---

## III. KẾT LUẬN SPRINT 2
Qua đợt kiểm thử chuyên sâu (Functional & Stress Test), sản phẩm mã nguồn P2PChat của nhóm đáp ứng hoàn hảo các tiêu chí đề ra. Cơ chế mã hóa bảo mật hoạt động an toàn dưới nền, kiến trúc đa luồng phân tách tác vụ mạng tốt giúp tối ưu hóa trải nghiệm người dùng trên GUI. Hệ thống sẵn sàng đóng gói cho các đợt phát triển tính năng tiếp theo.