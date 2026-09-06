# Analisis Sistem ROV — dokumentasi untuk engineer

Tujuan: Dokumen ini menyajikan analisis menyeluruh (high-level hingga detail implementasi) dari backend Project ROV Vision sehingga engineer lain dapat memahami, debugging, maintenance, dan pengembangan tanpa harus membaca seluruh source code baris demi baris.

Lokasi kode yang dianalisis (referensi):
- [main.py](/run/media/dapit/LAB/PROJECT ROV/ROV_Final/Backend/main.py)
- [config.py](/run/media/dapit/LAB/PROJECT ROV/ROV_Final/Backend/config.py)
- [requirements.txt](/run/media/dapit/LAB/PROJECT ROV/ROV_Final/Backend/requirements.txt)
- Core modules: [core/](/run/media/dapit/LAB/PROJECT ROV/ROV_Final/Backend/core)
  - [core/routes.py](/run/media/dapit/LAB/PROJECT ROV/ROV_Final/Backend/core/routes.py)
  - [core/websocket.py](/run/media/dapit/LAB/PROJECT ROV/ROV_Final/Backend/core/websocket.py)
  - [core/mavlink.py](/run/media/dapit/LAB/PROJECT ROV/ROV_Final/Backend/core/mavlink.py)
  - [core/telemetry.py](/run/media/dapit/LAB/PROJECT ROV/ROV_Final/Backend/core/telemetry.py)
  - [core/trajectory.py](/run/media/dapit/LAB/PROJECT ROV/ROV_Final/Backend/core/trajectory.py)
  - [core/autonomous.py](/run/media/dapit/LAB/PROJECT ROV/ROV_Final/Backend/core/autonomous.py)
  - [core/failsafe.py](/run/media/dapit/LAB/PROJECT ROV/ROV_Final/Backend/core/failsafe.py)
  - [core/logger.py](/run/media/dapit/LAB/PROJECT ROV/ROV_Final/Backend/core/logger.py)
- Camera processes: [camera_front/](/run/media/dapit/LAB/PROJECT ROV/ROV_Final/Backend/camera_front), [camera_bottom/](/run/media/dapit/LAB/PROJECT ROV/ROV_Final/Backend/camera_bottom)
  - [camera_front/stream_server.py](/run/media/dapit/LAB/PROJECT ROV/ROV_Final/Backend/camera_front/stream_server.py)
  - [camera_bottom/stream_server.py](/run/media/dapit/LAB/PROJECT ROV/ROV_Final/Backend/camera_bottom/stream_server.py)
- Dokumen misi/autonomous: [autonomous_intructions.md](/run/media/dapit/LAB/PROJECT ROV/ROV_Final/Backend/autonomous_intructions.md)

Catatan: Semua temuan berdasarkan isi kode sumber. Jika sesuatu tidak ditemukan di source code, ditandai dengan "Tidak ditemukan dalam source code".

--------------------------------------------------------------------------------
1) Struktur folder dan fungsi setiap file/module (ringkasan)
--------------------------------------------------------------------------------
- main.py
  - Entry point utama. Menjalankan 3 proses multiprocessing: Core (API + WebSocket), Camera Front server, Camera Bottom server. Mengatur logging terpusat dan graceful shutdown.

- config.py
  - Semua konstanta konfigurasi: port, camera index, FPS/resolusi, MAVLink string, ZMQ ports, parameter autonomous, PID/gain, paths untuk model, logging, dll.

- requirements.txt
  - Daftar dependensi utama: fastapi, uvicorn, python-socketio, pyzmq, pymavlink, pyzbar, aiortc, ultralytics (YOLO), python-dotenv, psutil.

- core/
  - routes.py: implementasi FastAPI REST dan Socket.IO event handlers; wiring komponen core (MAVLinkBridge, TelemetryManager, TrajectoryEstimator, FailsafeWatchdog, AutonomousController). Membuat ZMQ PUSH sockets untuk mengirim perintah ke proses kamera.
  - websocket.py: python-socketio AsyncServer, ZMQ SUB listener (asyncio) yang menerima topik dari kamera dan meneruskan event ke React + callback ke AutonomousController.
  - mavlink.py: MAVLinkBridge meng-handle koneksi pymavlink, reader thread, heartbeat thread, dan pengiriman command (arm/disarm, manual_control, rc_override, servo, relays).
  - telemetry.py: TelemetryManager memproses pesan MAVLink relevan (ATTITUDE, HEARTBEAT, BATTERY_STATUS, GPS, IMU, VFR_HUD) dan menyimpan state, menyediakan callback on_telemetry_update.
  - trajectory.py: Dead-reckoning trajectory estimator (integrasi velocity dari joystick/manual control + yaw) dan menyimpan path history; menyediakan snapshot untuk autonomous replay.
  - autonomous.py: FSM (MissionState) untuk misi autonomous: NAV_TO_WAYPOINT → SEARCH → ALIGN → APPROACH → GRAB → VERIFY_GRAB → RETURN → COMPLETE. Berinteraksi dengan YOLO detections, QR hasil, dan mengirim RC commands ke MAVLink.
  - failsafe.py: Watchdog & failsafe sistem; memonitor MAVLink heartbeat, dashboard connection, telemetry freshness, health camera (HTTP /health), sistem resource; dapat mencoba recovery, eskalasi ke CRITICAL, dan EMERGENCY (DISARM).
  - logger.py: Setup logging terpusat (console berwarna + file daily + error file) dan utility helper serta ROVLogger wrapper.

- camera_front/
  - stream_server.py: FastAPI server untuk streaming MJPEG + WebRTC offer endpoint; capture thread, ZMQ PUB untuk event (qr_front_result, camera_result, vision_front_result), ZMQ PULL untuk menerima perintah (screenshot, record, vision_activate, etc.). Menggunakan YOLO detector saat vision aktif.
  - modul lain: camera.py, image_processing.py, qr_detector.py, detector.py (YOLO), record.py, screenshot.py — berisi utilitas kamera, pemrosesan gambar, QR/YOLO, dan perekaman. (Detail fungsi ditemukan di file masing-masing — bukan semua dibaca penuh di analisis ini.)

- camera_bottom/
  - stream_server.py: Mirip camera_front, tapi fokus pada QR scanning & docking; ZMQ PUB topik: "qr_result" dan "dock_event".
  - modul lain: camera, image_processing, qr_detector, record, screenshot.

- storage/, logs/ dan test/ : storage untuk screenshot/recording, logs untuk file log.

--------------------------------------------------------------------------------
2) Alur data dan komunikasi antar komponen
--------------------------------------------------------------------------------
- Multiprocess architecture (lihat [main.py](/run/media/dapit/LAB/PROJECT ROV/ROV_Final/Backend/main.py))
  - Process 1: CoreAPI (FastAPI + Socket.IO) berjalan pada PORT_CORE_API (default 8000).
  - Process 2: CameraFront server (MJPEG + WebRTC) pada PORT_STREAM_FRONT (8001).
  - Process 3: CameraBottom server pada PORT_STREAM_BOTTOM (8002).

- IPC: ZeroMQ (lokal TCP pada 127.0.0.1)
  - camera_bottom PUB → core ZMQ SUB : topics seperti "qr_result", "dock_event".
  - camera_front PUB  → core ZMQ SUB : topics seperti "qr_front_result", "vision_front_result".
  - core PUSH → camera_front/camera_bottom PULL : perintah camera (screenshot, record_start/stop, vision_activate, dll.).

- MAVLink (pymavlink) berkomunikasi antara Core (MAVLinkBridge) dan Pixhawk/SITL.
  - MAVLinkBridge membaca pesan MAVLink, menyimpan heartbeat timestamp, dan men-trigger TelemetryManager callback.

- WebSocket (Socket.IO) mengirimkan telemetry_update, trajectory_update, qr_detected, camera_result, dan event autonomous ke client React.

--------------------------------------------------------------------------------
3) Arsitektur Raspberry Pi, Pixhawk, MAVLink, kamera, joystick, backend, frontend, dan autonomous system
--------------------------------------------------------------------------------
- Raspberry Pi: target deployment (lihat komentar config.py dan penggunaan psutil). Kamera terhubung sebagai /dev/videoX, indeks di [config.py](/run/media/dapit/LAB/PROJECT ROV/ROV_Final/Backend/config.py).
- Pixhawk / MAVLink: koneksi via config.MAVLINK_CONNECTION_STRING (default udp:0.0.0.0:14550), digunakan oleh [core/mavlink.py](/run/media/dapit/LAB/PROJECT ROV/ROV_Final/Backend/core/mavlink.py).
- Kamera: dua proses terpisah (front & bottom). bottom fokus QR/docking; front fokus vision (YOLO) + WebRTC.
- Joystick: frontend mengirim virtual channels dan core routes mengubahnya menjadi manual_control / rc_override (lihat mapping di [config.py] dan [routes.py]).
- Backend: CoreAPI (FastAPI + Socket.IO) sebagai orchestrator; camera processes menangani capture dan deteksi; MAVLinkBridge menghubungkan ke Pixhawk.
- Frontend: Tidak ada kode frontend di repo ini — asumsi React dashboard yang terhubung via Socket.IO (event names ditentukan di websocket.py dan routes.py).
- Autonomous system: implemented di [core/autonomous.py] — FSM yang menerima waypoint snapshot dari trajectory.py dan detections dari camera_front YOLO/qr.

--------------------------------------------------------------------------------
4) Semua endpoint REST/WebSocket serta fungsi masing-masing
--------------------------------------------------------------------------------
- REST (ditemukan di [core/routes.py](/run/media/dapit/LAB/PROJECT ROV/ROV_Final/Backend/core/routes.py)):
  - GET /api/status → status sistem + MAVLink
  - GET /api/streams → URL stream (front & bottom)
  - GET /api/telemetry → snapshot telemetry
  - GET /api/trajectory → snapshot trajectory
  - POST /api/trajectory/reset → reset posisi
  - GET /api/qr/history → riwayat QR
  - DELETE /api/qr/history → clear QR history
  - GET /api/health → health
  - POST /api/camera/{cam}/screenshot → trigger screenshot (cam = front|bottom)
  - POST /api/camera/{cam}/record/start → start recording
  - POST /api/camera/{cam}/record/stop → stop recording
  - GET /api/failsafe/status → snapshot failsafe
  - GET /api/failsafe/events → riwayat events
  - GET /api/autonomous/status → status autonomous
  - POST /api/trajectory/set_target → simpan target snapshot untuk replay

- Socket.IO events (server side handlers di routes.py dan emit event names di websocket.py):
  - Incoming (client → server): cmd_arm, cmd_disarm, cmd_set_mode, cmd_gripper, cmd_light, cmd_rc_override, cmd_emergency_stop, cmd_clear_emergency, cmd_autonomous_start, cmd_autonomous_stop
  - Outgoing (server → client) via sio.emit: telemetry_update, trajectory_update, mavlink_status, qr_detected, dock_aligned/dock_lost (topic names from ZMQ), camera_result, autonomous_status, mission_event, mission_complete, failsafe_event, failsafe_status, emergency_stop.

--------------------------------------------------------------------------------
5) Alur camera streaming dan QR/object detection
--------------------------------------------------------------------------------
- Kamera membaca frame di thread capture (_capture_loop di camera_front/stream_server.py dan camera_bottom/stream_server.py).
- Frame diproses oleh image processing pipeline (CLAHE, color-correction — di camera_front.image_processing / camera_bottom.image_processing).
- camera_bottom menjalankan QRDetector.scan() setiap frame sebelum overlay, mengirim hasil via ZMQ PUB topic "qr_result" dan "dock_event".
- camera_front memiliki YOLO detector ([camera_front/detector.py]) yang di-trigger saat _vision_active. Hasil detection dikirim via ZMQ topic "vision_front_result" dan juga digambar pada stream. YOLO model path: config.VISION_MODEL_PATH (default "models/rov_best.pt").
- Streaming: MJPEG endpoint /stream dan WebRTC /offer untuk browser peer (camera_* stream_server.py).

--------------------------------------------------------------------------------
6) Alur manual control dan autonomous control
--------------------------------------------------------------------------------
- Manual control
  - Frontend mengirim "cmd_rc_override" event berisi channel values.
  - [core/routes.py] melakukan rate limiting (~33Hz) dan memetakan frontend channel ke nilai x,y,z,r kemudian memanggil _mav.manual_control(...) dan mengupdate trajectory.update_velocity().

- Autonomous control
  - Operator membuat snapshot trajectory via POST /api/trajectory/set_target.
  - Operator memulai misi via cmd_autonomous_start (Socket.IO) dengan target_id.
  - [core/autonomous.py] memuat waypoints dari trajectory._replay_waypoints dan menjalankan FSM: navigasi ke area → search (yaw sweep sambil aktifkan vision) → align (P-controller berdasarkan bbox center) → approach → grab → verify → return → selesai.
  - Autonomous mengirim RC commands via _mav.manual_control atau rc_override mapping.
  - Vision input berasal dari camera_front YOLO (vision_front_result) dan QR dari camera_bottom (qr_front_result for front QR).

--------------------------------------------------------------------------------
7) State machine, trajectory, telemetry, failsafe, logging, dan komponen terkait
--------------------------------------------------------------------------------
- State machine autonomous: [core/autonomous.py] MissionState enum dan _mission_loop mengatur fase misi.
- Trajectory: [core/trajectory.py] dead-reckoning dan snapshot untuk replay.
- Telemetry: [core/telemetry.py] memproses MAVLink messages dan memicu callbacks ke routes.py.
- Failsafe: [core/failsafe.py] loop monitoring, health per subsistem, recovery attempts, escalation ke CRITICAL/EMERGENCY.
- Logging: [core/logger.py] setup_logging dipanggil di main.py; menyediakan file daily + error file dan console colored output.

--------------------------------------------------------------------------------
8) Dependency/library yang digunakan dan alasan penggunaannya
--------------------------------------------------------------------------------
(Didakonstruksi dari [requirements.txt](/run/media/dapit/LAB/PROJECT ROV/ROV_Final/Backend/requirements.txt))
- fastapi, uvicorn: REST + ASGI server
- python-socketio, python-engineio: Socket.IO real-time
- pyzmq: IPC (ZMQ) untuk komunikasi antar-proses
- pymavlink: komunikasi MAVLink dengan Pixhawk
- pyzbar: decoding QR code (alternatif/penunjang)
- aiortc: WebRTC
- ultralytics: YOLO object detection (vision)
- python-dotenv: konfigurasi via .env (opsional)
- psutil: monitoring system resources oleh failsafe

Alasan: library dipilih untuk kebutuhan nyata — ASGI + Socket.IO untuk real-time dashboard, ZMQ untuk IPC performa tinggi antar proses, pymavlink untuk komunikasi low-level ke autopilot, aiortc untuk WebRTC low-latency streaming, dan ultralytics untuk object detection.

--------------------------------------------------------------------------------
9) Threading, multiprocessing, queue, async, dan komunikasi antar-process
--------------------------------------------------------------------------------
- Multiprocessing (main.py): tiga proses terpisah (CoreAPI, CameraFront, CameraBottom). start_method=spawn.
- Di setiap proses:
  - Core: menjalankan FastAPI (uvicorn) dengan event loop asyncio; ZMQ listener dibuat sebagai asyncio task (_zmq_listener_loop di core/websocket.py). Beberapa komponen seperti MAVLinkBridge, TelemetryManager, TrajectoryEstimator, FailsafeWatchdog, AutonomousController berjalan dengan thread internal (MAVLink reader/heartbeat threads, failsafe background thread, autonomous mission thread).
  - Camera processes: capture_loop thread (membaca frame), ZMQ command loop thread (menerima perintah dari core), dan ASGI event loop untuk WebRTC endpoints.
- Queue-like bridges:
  - ZmqQueueWrapper mengemas publish ke ZMQ PUB socket dari camera modules.
  - Internal Queue/Multiprocessing.Queue digunakan antara core dan autonomous (di routes.py dan core.autonomous init).
- Locks: banyak penggunaan threading.Lock dan RLock untuk melindungi state bersama: telemetry, trajectory, frame buffers, zmq send.

--------------------------------------------------------------------------------
10) Konfigurasi penting, environment variable, port, device, dan parameter hardware
--------------------------------------------------------------------------------
- Ports:
  - Core API: config.PORT_CORE_API (8000)
  - Stream Front: config.PORT_STREAM_FRONT (8001)
  - Stream Bottom: config.PORT_STREAM_BOTTOM (8002)
  - ZMQ ports: ZMQ_PORT_BOTTOM_PUB=5555, ZMQ_PORT_FRONT_PUB=5556, ZMQ_PORT_BOTTOM_CMD=5557, ZMQ_PORT_FRONT_CMD=5558
- MAVLink config:
  - MAVLINK_CONNECTION_STRING (default udp:0.0.0.0:14550), MAVLINK_BAUD
- Camera indices: CAMERA_FRONT_INDEX, CAMERA_BOTTOM_INDEX
- YOLO model path: VISION_MODEL_PATH (models/rov_best.pt)
- Throttles, PID/gain: banyak konstanta autonomous seperti KP, threshold, timeouts (lihat [config.py](/run/media/dapit/LAB/PROJECT ROV/ROV_Final/Backend/config.py)).

--------------------------------------------------------------------------------
11) Alur eksekusi program dari startup sampai runtime
--------------------------------------------------------------------------------
- Jalankan `python main.py` atau `python3 main.py`.
- main.py memanggil setup_logging(), kemudian spawn 3 proses: CoreAPI, CameraFront, CameraBottom.
- Camera processes melakukan inisialisasi kamera, detektor, membuka ZMQ PUB, dan start capture thread + ZMQ command thread, lalu menjalankan uvicorn FastAPI (stream server).
- Core process membuat MAVLinkBridge, TelemetryManager, TrajectoryEstimator, FailsafeWatchdog, AutonomousController, membuat ZMQ PUSH sockets (bind) untuk mengirim perintah ke kamera, dan menjalankan uvicorn ASGI app (Socket.IO + FastAPI). Pada startup FastAPI juga memulai ZMQ SUB listener (async) untuk menerima events dari kamera.
- Selama runtime: camera publish events via ZMQ, core menerima dan meneruskan ke frontend via Socket.IO; frontend mengirim perintah via Socket.IO yang diterjemahkan ke MAVLink atau dikirim ke kamera via REST endpoints atau routes yang mendorong perintah ke ZMQ PUSH.

--------------------------------------------------------------------------------
12) Dependency antar-module dan potensi circular dependency
--------------------------------------------------------------------------------
- Wiring utama dilakukan di [core/routes.py] ketika membuat objek (MAVLinkBridge, TelemetryManager, TrajectoryEstimator, FailsafeWatchdog, AutonomousController) dan kemudian mem-pass reference antar modul. Bentuk ini mengurangi import-time circular references.
- Potensi circular:
  - modules saling mengimpor core.logger dan config (normal). Saya tidak menemukan import cyclic eksplisit, tetapi hati-hati pada: logger.setup_logging() dipanggil dari main.py sebelum modul lain melakukan impor yang bergantung pada logger state.
- Kesimpulan: Tidak ditemukan circular dependency yang jelas dalam modul inti.

--------------------------------------------------------------------------------
13) Error handling dan failure scenario yang sudah ditangani
--------------------------------------------------------------------------------
- MAVLinkBridge: retry connect dengan timeout + menangani BAD_DATA frames, melindungi callback agar tidak crash reader thread.
- TelemetryManager: memfilter heartbeat dari autopilot component id, low-pass filter pada depth.
- Core routes: rate-limiting pada cmd_rc_override (perbaikan BUG-6) untuk mencegah banjir MAVLink.
- Camera servers: try/except di loop command dan capture untuk mencegah thread crash.
- FailsafeWatchdog: monitoring terperinci dan strategi recovery (reconnect MAVLink, escalate -> rc neutral, or emergency stop + disarm).

--------------------------------------------------------------------------------
14) Potensi bug, race condition, bottleneck, memory leak, latency, dan masalah concurrency
--------------------------------------------------------------------------------
- Global frame buffer (_display_frame, _raw_frame) di camera_* guarded by _frame_lock — ada proteksi lock, tetapi: pengecekan/penyalinan kadang dilakukan tanpa copy deep (mis. using .copy() done in some places), perlu memastikan tidak ada akses race saat sending frame untuk screenshot.
- ZMQ usage: core binds PUSH sockets and camera binds PUB sockets. Semua ZMQ binding di localhost. Jika proses start order berbeda kemungkinan pesan hilang karena PUB/SUB semantics (PUB sebelum SUB connect dapat drop). Namun core binds PUSH and cameras connect PULL — order handled in main.py since processes spawn roughly same time; tetapi PUB/SUB reliability (PUB starts before SUB connected) bisa menyebabkan missed initial events.
- Potential blocking: many synchronous blocking calls inside async context (e.g., in routes.py using asyncio.run_coroutine_threadsafe but some heavy sync code may block threads). Uvicorn configured log_level warning — normal.
- Rate limiting: cmd_rc_override has locking and time check — but precision of time.sleep-based loops and possible message drop under heavy load.
- Resource usage: YOLO (ultralytics) can be heavy on CPU/GPU — psutil used to monitor but no adaptive throttling.
- Thread termination: main._shutdown calls p.terminate() and then p.kill() if unresponsive. Some child processes may not cleanup resources gracefully.

--------------------------------------------------------------------------------
15) Security issue dan attack surface
--------------------------------------------------------------------------------
- ZMQ endpoints bind to 127.0.0.1 — local-only, ini mengurangi remote attack surface. Namun REST/WebRTC/Socket.IO servers bind 0.0.0.0 (public) — tergantung jaringan RPi harus dienkripsi dan diakses melalui jaringan tepercaya.
- Tidak ada authentication pada REST API atau Socket.IO handlers — operator UI assumed trusted. Ini berarti anyone on network can arm/disarm, trigger recording, atau menjalankan autonomous mission.
- WebRTC signalling (/offer) tidak mengautentikasi origin — potensi akses tidak sah.
- MAVLink connection default udp:0.0.0.0:14550 — perlu berhati-hati bila Pixhawk/SITL accessible dari jaringan.

--------------------------------------------------------------------------------
16) Bagian yang fragile, hardcoded, redundant, atau sulit di-maintain
--------------------------------------------------------------------------------
- Banyak konstanta hardcoded di config.py; bagus karena sentral, tetapi beberapa nilai sensitif (ports, model path) tidak di-override via env secara konsisten.
- Assumsi binding address 127.0.0.1 untuk ZMQ dan 0.0.0.0 untuk ASGI servers — konfigurasi jaringan perlu dokumentasi operasi.
- Handling PUB/SUB startup race: PUB/SUB semantics rentan pesan hilang saat koneksi belum terbentuk.

--------------------------------------------------------------------------------
17) Komponen yang belum selesai, TODO, placeholder, atau implementasi yang tampak belum lengkap
--------------------------------------------------------------------------------
- Saya menemukan file [autonomous_intructions.md](/run/media/dapit/LAB/PROJECT ROV/ROV_Final/Backend/autonomous_intructions.md) (dokumen instruksi) namun tidak semua langkah eksekusi misi diuji di kode (mis. verifikasi sensor lingkungan). Rincian implementasi di beberapa modul detektor (camera/*) tidak dibaca penuh di analisis ini.
- Jika ada tests/integration tests: folder test/ ada, namun cakupan test tidak dianalisis di sini.
- Jika diperlukan, bisa jalankan test runner untuk memverifikasi.

--------------------------------------------------------------------------------
18) Inkonsistensi antara dokumentasi, konfigurasi, dan implementasi source code
--------------------------------------------------------------------------------
- Komentar di beberapa file menyebut penggunaan Flask/Flask-SocketIO namun implementasi menggunakan FastAPI + python-socketio (kode sudah memakai FastAPI/uvicorn) — ini bisa membingungkan (lihat header di routes.py yang menyebut Flask di komentar). Rekomendasi: sinkronisasi komentar.

--------------------------------------------------------------------------------
19) Jelaskan bagian penting dengan potongan kode dan referensi file + fungsi/class terkait
--------------------------------------------------------------------------------
- Entry point spawning processes (main.py):

  from [main.py](/run/media/dapit/LAB/PROJECT ROV/ROV_Final/Backend/main.py):
  - proses CoreAPI: run_core_server (didefinisikan di core/routes.py)
  - proses CameraFront: run_front_stream_server (camera_front/stream_server.py)
  - proses CameraBottom: run_bottom_stream_server (camera_bottom/stream_server.py)

- ZMQ wiring (core/routes.py):
  _zmq_push_front = _zmq_ctx.socket(zmq.PUSH)
  _zmq_push_front.bind(f"tcp://127.0.0.1:{ZMQ_PORT_FRONT_CMD}")
  _zmq_push_bottom = _zmq_ctx.socket(zmq.PUSH)
  _zmq_push_bottom.bind(f"tcp://127.0.0.1:{ZMQ_PORT_BOTTOM_CMD}")

  Dan di camera_front/stream_server.py:
  _zmq_pub.bind(f"tcp://127.0.0.1:{ZMQ_PORT_FRONT_PUB}")
  _zmq_pull_cmd.connect(f"tcp://127.0.0.1:{ZMQ_PORT_FRONT_CMD}")

- MAVLink reader loop (core/mavlink.py):
  msg = self._conn.recv_match(blocking=True, timeout=1.0)
  if msg.get_type() == 'BAD_DATA': continue
  if msg.get_type() == 'HEARTBEAT': self._last_heartbeat_time = time.time()
  if self.on_message_callback: self.on_message_callback(msg)

- Autonomous loop phases (core/autonomous.py):
  _mission_loop() orchestrates phases: _phase_nav_to_waypoint(), _phase_search(), _phase_align(), _phase_approach(), _phase_grab(), _phase_verify_grab(), _phase_return().

--------------------------------------------------------------------------------
20) Rekomendasi improvement berdasarkan prioritas (Critical → High → Medium → Low)
--------------------------------------------------------------------------------
- Critical
  1. Tambahkan authentication & authorization untuk REST + Socket.IO endpoints (minimal token-based) — saat ini siapa pun di jaringan dapat memanggil cmd_arm, cmd_disarm, emergency, dll.
  2. Pastikan emergency stop & failsafe diuji end-to-end di hardware sebelum operasi (DISARM reliability, RC override behavior).
  3. Pastikan MAVLink connection string dan binding port tidak terekspos di jaringan publik.

- High
  1. Perbaiki potensi race PUB/SUB ZMQ (gunakan XPUB/XSUB atau implementasikan reconnect/publish buffering) atau gunakan IPC transport (ipc://) jika pada mesin yang sama untuk mengurangi pesan hilang.
  2. Tambahkan back-pressure handling untuk YOLO/detector (batasi frame rate saat vision aktif) agar CPU tidak jenuh.
  3. Konsistenkan konfigurasi via env files (.env) dan dokumentasikan variabel yang harus diset pada deployment (MAVLINK_CONNECTION_STRING, PORTs, CAMERA_INDEX).

- Medium
  1. Tambahkan healthchecks/instrumentation lebih lengkap (metrics prometheus) dan log rotation policy yang mudah diexport.
  2. Tambahkan unit/integration tests untuk modul kritis: mavlink connect, telemetry parsing, trajectory snapshot, autonomous FSM (mocked dependencies).
  3. Perbaiki komentar/usability inconsistencies (mis. komentar yang menyebut Flask sambil menggunakan FastAPI).

- Low
  1. Refactor beberapa utility menjadi modul terpisah (mis. ZmqQueueWrapper reuse antara camera_front & bottom sudah ada, namun bisa ekstrak ke core/comm untuk mengurangi duplikasi).
  2. Tambahkan contoh deployment script / systemd unit file untuk tiap proses agar mudah autostart pada RPi.
  3. Dokumentasi developer: README singkat langkah setup dev environment, model download, dan run instructions.

--------------------------------------------------------------------------------
Kesimpulan dan langkah berikutnya yang disarankan
--------------------------------------------------------------------------------
- Dokumen ini dibuat langsung dari kode sumber yang ada. Beberapa bagian (mis. kualitas/presisi estimator posisi bawah air, parameter tuning YOLO, atau detail hardware wiring) TIDAK DITEMUKAN DALAM SOURCE CODE dan perlu verifikasi lapangan atau dokumentasi hardware.
- Langkah berikutnya (opsional):
  1. Jalankan test integrasi di lingkungan SITL untuk memverifikasi MAVLink/failsafe/autonomous.
  2. Jalankan sistem di dev RPi dan pantau logs/psutil untuk tuning resource.
  3. Terapkan rekomendasi security (auth) dan PUB/SUB reliability.

Jika mau, langkah selanjutnya saya bisa:
- Ekstrak daftar TODO dan buat issue/todo items terstruktur.
- Membuat checklist deployment (systemd unit files) dan script untuk men-download model YOLO jika belum ada.
- Menambah contoh .env atau dokumentasi setup lebih rinci.

-- Akhir analisa --
