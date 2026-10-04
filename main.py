import json
import queue
import sys
import threading
import time
import pydirectinput
from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
import sounddevice as sd
from vosk import KaldiRecognizer, Model

CONFIG_FILE = "commands.json"
MODEL_PATH = "model"
pydirectinput.PAUSE = 0.02


class SignalBridge(QObject):
  phrase_detected = Signal(str, str)
  status_changed = Signal(str, str)


class VoiceThread(threading.Thread):

  def __init__(self, device_id, commands, bridge):
    super().__init__(daemon=True)
    self.device_id = device_id
    self.commands = commands
    self.bridge = bridge
    self.running = True
    self.audio_queue = queue.Queue()

  def callback(self, indata, frames, time_info, status):
    self.audio_queue.put(bytes(indata))

  def run(self):
    try:
      model = Model(MODEL_PATH)
    except Exception:
      self.bridge.status_changed.emit("Папка 'model' не найдена!", "error")
      return

    words = list(self.commands.keys())
    if not words:
      self.bridge.status_changed.emit("Добавьте хотя бы одну команду!", "error")
      return

    grammar = json.dumps(words + ["[unk]"], ensure_ascii=False)
    rec = KaldiRecognizer(model, 16000, grammar)

    try:
      with sd.RawInputStream(
          samplerate=16000,
          blocksize=4000,
          device=self.device_id,
          dtype="int16",
          channels=1,
          callback=self.callback,
      ):
        self.bridge.status_changed.emit("Слушаю команды...", "active")
        while self.running:
          data = self.audio_queue.get()
          if rec.AcceptWaveform(data):
            res = json.loads(rec.Result())
            text = res.get("text", "").strip()
            if text and text in self.commands:
              key = self.commands[text]
              self.bridge.phrase_detected.emit(text, key)
              self.press_key(key)
    except Exception as e:
      self.bridge.status_changed.emit(f"Ошибка аудио: {e}", "error")

  def press_key(self, key_name):
    try:
      keys = [k.strip().lower() for k in key_name.split("+")]
      for k in keys:
        pydirectinput.keyDown(k)
      time.sleep(0.04)
      for k in reversed(keys):
        pydirectinput.keyUp(k)
    except Exception as err:
      print(f"Ошибка нажатия: {err}")

  def stop(self):
    self.running = False


class App(QMainWindow):

  def __init__(self):
    super().__init__()
    self.setWindowFlags(Qt.FramelessWindowHint)
    self.setAttribute(Qt.WA_TranslucentBackground)
    self.resize(520, 690)

    self.bridge = SignalBridge()
    self.bridge.phrase_detected.connect(self.on_phrase)
    self.bridge.status_changed.connect(self.on_status)

    self.drag_position = QPoint()
    self.voice_thread = None
    self.commands = self.load_commands()

    self.apply_styles()
    self.init_ui()

  def apply_styles(self):
    self.setStyleSheet("""
            QWidget#MainContainer {
                background-color: #121417;
                border: 1px solid #282C34;
                border-radius: 12px;
            }
            QWidget {
                color: #E2E8F0;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-size: 13px;
            }
            QFrame.card {
                background-color: #1A1D24;
                border: 1px solid #282C34;
                border-radius: 10px;
            }
            QLabel.sectionTitle {
                font-size: 11px;
                font-weight: 700;
                color: #8E96A4;
                letter-spacing: 0.5px;
                text-transform: uppercase;
            }
            QComboBox, QLineEdit {
                background-color: #222630;
                border: 1px solid #333842;
                border-radius: 6px;
                padding: 7px 12px;
                color: #FFFFFF;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background-color: #1A1D24;
                selection-background-color: #3B82F6;
                color: #FFFFFF;
                border: 1px solid #333842;
            }
            QTableWidget {
                background-color: #1A1D24;
                border: 1px solid #282C34;
                border-radius: 8px;
                gridline-color: #222630;
                color: #FFFFFF;
            }
            QHeaderView::section {
                background-color: #15181E;
                color: #8E96A4;
                padding: 6px;
                border: none;
                font-weight: 600;
            }
            QPushButton {
                font-weight: 600;
                border-radius: 6px;
                padding: 8px 14px;
            }
            QPushButton.btnPrimary {
                background-color: #3B82F6;
                color: white;
                border: none;
            }
            QPushButton.btnPrimary:hover { background-color: #2563EB; }
            QPushButton.btnDanger {
                background-color: rgba(239, 68, 68, 0.15);
                border: 1px solid rgba(239, 68, 68, 0.3);
                color: #F87171;
            }
            QPushButton.btnDanger:hover {
                background-color: #EF4444;
                color: white;
            }
            /* Кнопки заголовка */
            QPushButton.titleBtn {
                background: transparent;
                border: none;
                color: #94A3B8;
                font-size: 14px;
                border-radius: 4px;
                padding: 2px 8px;
            }
            QPushButton.titleBtn:hover {
                background-color: #282C34;
                color: white;
            }
            QPushButton.closeBtn:hover {
                background-color: #EF4444;
                color: white;
            }
        """)

  def init_ui(self):
    container = QWidget()
    container.setObjectName("MainContainer")
    self.setCentralWidget(container)

    main_layout = QVBoxLayout(container)
    main_layout.setContentsMargins(16, 10, 16, 16)
    main_layout.setSpacing(12)

    # Кастомная шапка (Titlebar)
    title_bar = QHBoxLayout()
    title_bar.setContentsMargins(4, 2, 0, 4)

    app_title = QLabel("🎙️ Allods Voice Control")
    app_title.setStyleSheet("font-weight: 700; font-size: 13px; color: #CBD5E1;")
    title_bar.addWidget(app_title)
    title_bar.addStretch()

    btn_min = QPushButton("🗕")
    btn_min.setProperty("class", "titleBtn")
    btn_min.clicked.connect(self.showMinimized)

    btn_close = QPushButton("✕")
    btn_close.setProperty("class", "titleBtn closeBtn")
    btn_close.clicked.connect(self.close)

    title_bar.addWidget(btn_min)
    title_bar.addWidget(btn_close)
    main_layout.addLayout(title_bar)

    # 1. Карточка микрофона
    mic_card = QFrame()
    mic_card.setProperty("class", "card")
    mic_layout = QVBoxLayout(mic_card)
    mic_title = QLabel("ИСТОЧНИК ЗВУКА")
    mic_title.setProperty("class", "sectionTitle")
    mic_layout.addWidget(mic_title)

    self.mic_combo = QComboBox()
    self.populate_mics()
    mic_layout.addWidget(self.mic_combo)
    main_layout.addWidget(mic_card)

    # 2. Карточка команд
    table_card = QFrame()
    table_card.setProperty("class", "card")
    table_layout = QVBoxLayout(table_card)
    table_title = QLabel("БИНДЫ И КОМАНДЫ")
    table_title.setProperty("class", "sectionTitle")
    table_layout.addWidget(table_title)

    self.table = QTableWidget(0, 2)
    self.table.setHorizontalHeaderLabels(["Слово / Фраза", "Клавиша"])
    self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    self.table.verticalHeader().setVisible(False)
    table_layout.addWidget(self.table)
    self.refresh_table()

    inputs_layout = QHBoxLayout()
    self.input_word = QLineEdit()
    self.input_word.setPlaceholderText("Слово (щит, стан)")
    self.input_key = QLineEdit()
    self.input_key.setPlaceholderText("Клавиша (q, e, shift+1)")
    btn_add = QPushButton("Добавить")
    btn_add.setProperty("class", "btnPrimary")
    btn_add.clicked.connect(self.add_command)

    inputs_layout.addWidget(self.input_word, 2)
    inputs_layout.addWidget(self.input_key, 2)
    inputs_layout.addWidget(btn_add, 1)
    table_layout.addLayout(inputs_layout)

    btn_del = QPushButton("Удалить выбранную строку")
    btn_del.setProperty("class", "btnDanger")
    btn_del.clicked.connect(self.del_command)
    table_layout.addWidget(btn_del)
    main_layout.addWidget(table_card)

    # 3. Карточка статуса и запуска
    ctrl_card = QFrame()
    ctrl_card.setProperty("class", "card")
    ctrl_layout = QVBoxLayout(ctrl_card)

    status_row = QHBoxLayout()
    self.status_dot = QLabel("●")
    self.status_dot.setStyleSheet("color: #64748B; font-size: 15px;")
    self.status_text = QLabel("Готов к работе")
    self.status_text.setStyleSheet("color: #94A3B8; font-weight: 600;")
    status_row.addWidget(self.status_dot)
    status_row.addWidget(self.status_text)
    status_row.addStretch()
    ctrl_layout.addLayout(status_row)

    self.btn_toggle = QPushButton("СТАРТ")
    self.btn_toggle.setFixedHeight(44)
    self.btn_toggle.setStyleSheet("""
            QPushButton {
                background-color: #10B981;
                color: white;
                font-size: 14px;
                font-weight: 700;
                border: none;
                border-radius: 8px;
            }
            QPushButton:hover { background-color: #059669; }
        """)
    self.btn_toggle.clicked.connect(self.toggle_listening)
    ctrl_layout.addWidget(self.btn_toggle)

    self.log_label = QLabel("Последнее действие: —")
    self.log_label.setStyleSheet(
        "color: #64748B; font-size: 11px; margin-top: 4px;"
    )
    ctrl_layout.addWidget(self.log_label)
    main_layout.addWidget(ctrl_card)

  # Перетаскивание безрамочного окна мышкой
  def mousePressEvent(self, event):
    if event.button() == Qt.LeftButton:
      self.drag_position = (
          event.globalPosition().toPoint() - self.frameGeometry().topLeft()
      )
      event.accept()

  def mouseMoveEvent(self, event):
    if event.buttons() == Qt.LeftButton and not self.drag_position.isNull():
      self.move(event.globalPosition().toPoint() - self.drag_position)
      event.accept()

  def populate_mics(self):
    devices = sd.query_devices()
    default_in = sd.default.device[0]
    added_names = set()
    for idx, dev in enumerate(devices):
      if dev["max_input_channels"] > 0:
        name = dev["name"]
        if name not in added_names:
          added_names.add(name)
          is_def = " (По умолчанию)" if idx == default_in else ""
          self.mic_combo.addItem(f"{name}{is_def}", idx)

  def load_commands(self):
    try:
      with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)
    except Exception:
      return {"стан": "e", "хил": "r", "щит": "q", "ульта": "x"}

  def save_commands(self):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
      json.dump(self.commands, f, ensure_ascii=False, indent=2)

  def refresh_table(self):
    self.table.setRowCount(0)
    for row, (w, k) in enumerate(self.commands.items()):
      self.table.insertRow(row)
      self.table.setItem(row, 0, QTableWidgetItem(w))
      self.table.setItem(row, 1, QTableWidgetItem(k))

  def add_command(self):
    w = self.input_word.text().strip().lower()
    k = self.input_key.text().strip().lower()
    if not w or not k:
      return
    self.commands[w] = k
    self.save_commands()
    self.refresh_table()
    self.input_word.clear()
    self.input_key.clear()

  def del_command(self):
    curr = self.table.currentRow()
    if curr >= 0:
      w = self.table.item(curr, 0).text()
      if w in self.commands:
        del self.commands[w]
        self.save_commands()
        self.refresh_table()

  def toggle_listening(self):
    if self.voice_thread and self.voice_thread.is_alive():
      self.voice_thread.stop()
      self.btn_toggle.setText("СТАРТ")
      self.btn_toggle.setStyleSheet("""
                QPushButton {
                    background-color: #10B981;
                    color: white;
                    font-size: 14px;
                    font-weight: 700;
                    border: none;
                    border-radius: 8px;
                }
                QPushButton:hover { background-color: #059669; }
            """)
      self.on_status("Остановлено", "idle")
    else:
      dev_idx = self.mic_combo.currentData()
      self.voice_thread = VoiceThread(dev_idx, self.commands, self.bridge)
      self.voice_thread.start()
      self.btn_toggle.setText("ОСТАНОВИТЬ")
      self.btn_toggle.setStyleSheet("""
                QPushButton {
                    background-color: #EF4444;
                    color: white;
                    font-size: 14px;
                    font-weight: 700;
                    border: none;
                    border-radius: 8px;
                }
                QPushButton:hover { background-color: #DC2626; }
            """)

  def on_phrase(self, phrase, key):
    self.log_label.setText(
        f"Последнее действие: Сказано «{phrase}» ➔ нажато [{key}]"
    )

  def on_status(self, text, state):
    self.status_text.setText(text)
    if state == "active":
      self.status_dot.setStyleSheet("color: #10B981; font-size: 15px;")
      self.status_text.setStyleSheet("color: #10B981; font-weight: 600;")
    elif state == "error":
      self.status_dot.setStyleSheet("color: #EF4444; font-size: 15px;")
      self.status_text.setStyleSheet("color: #EF4444; font-weight: 600;")
    else:
      self.status_dot.setStyleSheet("color: #64748B; font-size: 15px;")
      self.status_text.setStyleSheet("color: #94A3B8; font-weight: 600;")

  def closeEvent(self, event):
    if self.voice_thread and self.voice_thread.is_alive():
      self.voice_thread.stop()
    event.accept()


if __name__ == "__main__":
  app = QApplication(sys.argv)
  window = App()
  window.show()
  sys.exit(app.exec())