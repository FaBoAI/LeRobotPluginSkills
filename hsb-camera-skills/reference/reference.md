# LeRobot 0.6.0 HSB カメラプラグイン実装知見

Jetson AGX Thor + VB1940 Eagle + LeRobot 0.6.0 の実機構築(2026-08)で確認した内容。

## 1. カメラプラグイン検出の仕組みと命名規約

- LeRobot 0.6.0 のプラグイン発見は**エントリポイントではない**。
  `lerobot/utils/import_utils.py` の `register_third_party_plugins()` が
  `importlib.metadata.distributions()` を走査し、配布名が
  `lerobot_robot_` / `lerobot_camera_` / `lerobot_teleoperator_` / `lerobot_policy_` /
  `lerobot_env_` で始まるものを **配布名のまま** `importlib.import_module()` する。
  → 配布名 = トップレベルパッケージ名(アンダースコア維持)が必須。
- `lerobot-record` / `lerobot-teleoperate` は main() 冒頭で
  `register_third_party_plugins()` を呼ぶので、pip install 済みなら
  `--robot.cameras='{"cam": {"type": "hsb", ...}}'` がそのまま通る。`--plugins` フラグは無い。
- 組み込み以外のカメラ型は `make_cameras_from_configs()` →
  `make_device_from_device_class(cfg)` で解決される: Config クラス名から末尾
  `Config` を除いた名前(`HSBCameraConfig`→`HSBCamera`)を、(a) Config の親パッケージ
  (b) 親 + `.` + クラス名小文字 (c) `config_` プレフィックスを除いた隣接モジュール、
  の順で探す。**`__init__.py` での re-export が候補 (a) を満たす最短経路**。
- 登録は `@CameraConfig.register_subclass("hsb")` + `@dataclass`。
  base の `CameraConfig` は kw_only dataclass で `fps` / `width` / `height` を持つ。

## 2. LeRobot 0.6.0 Camera 抽象クラス(正確なシグネチャ)

```python
class Camera(abc.ABC):
    def __init__(self, config: CameraConfig):  # self.fps/width/height を設定
    @property @abstractmethod def is_connected(self) -> bool
    @staticmethod @abstractmethod def find_cameras() -> list[dict[str, Any]]
    @abstractmethod def connect(self, warmup: bool = True) -> None
    @abstractmethod def read(self) -> NDArray[Any]
    @abstractmethod def async_read(self, timeout_ms: float = ...) -> NDArray[Any]
    def read_latest(self, max_age_ms: int = 500) -> NDArray[Any]  # 未実装だと FutureWarning
    @abstractmethod def disconnect(self) -> None
```

- LeRobot 0.6.0 は **Python >=3.12、numpy>=2.0,<2.3** を要求(classifiers は 3.12/3.13)。
- エラー型は `lerobot.utils.errors` の `DeviceAlreadyConnectedError` /
  `DeviceNotConnectedError` を使うと組み込みカメラと挙動が揃う。
- `ColorMode` は `lerobot.cameras.configs` の `RGB`/`BGR`。

## 3. プロセス分離ブリッジ(本プラグインの核)

### なぜ必要か

| | hololink スタック | LeRobot 0.6.0 |
|---|---|---|
| Python | cpython-312 固定(holoscan deb バインディング) | >=3.12 |
| numpy | 1.26.0(公式コンテナ準拠。2.x は未検証域) | >=2.0,<2.3 |
| その他 | cupy-cuda13x, cuda-python, LD_LIBRARY_PATH 要 | torch スタック |

同一プロセス/venv に同居させると numpy の版が衝突する。プラグインは
**hololink 専用 venv でワーカーを subprocess 起動**し、フレームだけを受け取る。

### 共有メモリ seqlock プロトコル(検証済み仕様)

- `/dev/shm` 直下の**ファイルバック mmap**(`multiprocessing.shared_memory` は
  attach 側プロセス終了時に resource_tracker が segment を勝手に unlink する
  既知問題があるため使わない)。
- レイアウト(little-endian、ヘッダ 64 バイト固定):

```
offset 0:  magic   u32 = 0x48534243 ("HSBC")
offset 4:  version u32 = 1
offset 8:  width   u32
offset 12: height  u32
offset 16: channels u32 (3 = RGB8)
offset 20: fps     u32
offset 24: seq     u64  ← 奇数 = 書き込み中 / 偶数 = 安定
offset 32: ts      f64  (time.time())
offset 64: フレーム本体 (width*height*3 bytes, RGB8)
```

- 書き込み(ワーカー): seq を奇数に → フレーム+ts 書き込み → seq を偶数に。
- 読み出し(プラグイン): seq が偶数になるまで再読 → コピー → seq 再確認、
  変わっていたらやり直し(有限回リトライ)。
- `async_read` は「前回返した seq と異なる偶数 seq」を待つことで新フレームを保証。
- 実測: 2560×1984 RGB8(15MB/frame)で 30.2fps 安定(コピー1回)。

### ワーカーのライフサイクル

- 起動: プラグインが `Popen([hsb_venv_python, hsb_worker.py, ...])`。
  env は**明示的に構築**する: `PYTHONPATH=/opt/nvidia/holoscan/python/lib`、
  `LD_LIBRARY_PATH=/opt/nvidia/holoscan/lib:/usr/lib/aarch64-linux-gnu/nvidia`、
  `PATH` に hsb venv/bin(ワーカーが `hololink set-ip` CLI を spawn するため)。
- ハンドシェイク: ワーカーは**最初のフレームを書いた後**に stdout へ
  `READY <w> <h> <fps>` を出力。プラグインはこれをタイムアウト付きで待つ
  (enumeration + センサー設定を含むので余裕を持って 90 秒)。
- ワーカーの stderr は常時ドレインして直近 N 行を保持し、失敗時の
  ConnectionError に添える(リンク断の一次切り分けがエラーメッセージだけで済む)。
- 停止: SIGTERM → ワーカーは stop フラグ経由で holoscan の BooleanCondition を
  disable_tick して正常終了(`hololink.stop()` まで実行される)。猶予後 SIGKILL。

## 4. hololink 側の落とし穴

- **`hololink.reset()` は 10G リンクを落とす**: reset はカメラ側 PHY を再起動させ、
  リンク訓練が不安定な構成では再確立に失敗し `Device ... not found` になる。
  ワーカーのデフォルトは reset なし(`--reset` オプトイン)。センサーのリセットは
  レジスタ 0x8 への 0x0/0x1 書き込みで別途行われるため実用上問題ない。
- **`hololink set-ip` デーモンのリーク**: 公式 example は enumeration 成功後にしか
  `proc.terminate()` しないため、失敗パスでデーモンが残り UDP 12267 を掴み続ける。
  try/except で失敗時も必ず terminate すること。
- enumeration は UDP **12267** への BOOTP ブロードキャスト受信(非特権)。
  `Enumerator.find_channel()` のタイムアウトは約 20 秒。
- `import hololink` は無条件でネイティブ拡張(`_hololink` 等)を読む。
  純 Python 部分だけの利用は不可 — venv 分離が唯一の現実解。
- 非 root 実行では DataChannel 生成時に `SIOCSARP operation failed` の ERROR
  ログが出るが**非致命**(カーネルの通常 ARP で解決される)。

## 5. テスト戦略

- **fake_worker**: 実ワーカーと同じ CLI 引数・同じ SHM プロトコルで合成フレーム
  (フレームごとに変化するグラデーション)を配信するスクリプト。これにより
  プラグイン側の全ロジック(接続・seqlock・色変換・タイムアウト・後始末)が
  ハードウェアゼロで pytest できる。`--fail` で接続失敗パスも模擬。
- Config に `worker_script`(差し替え用)と `hsb_python`(テストでは
  `sys.executable`)を持たせるのがテスト容易性の鍵。
- 統合スモーク: `register_third_party_plugins()` → `CameraConfig.get_choice_class("hsb")`
  → `make_cameras_from_configs()` → connect → async_read ×N → disconnect。

## 6. 運用ノウハウ(Jetson / 10GigE)

- **cwd の罠**: プロジェクトの親ディレクトリを cwd にして python を起動すると、
  プロジェクトルート(パッケージと同名)が名前空間パッケージとして editable
  install を隠し `ImportError: unknown location` になる。
- インストールは `pip install -e . --no-deps`(Jetson の CUDA torch/numpy 保護)。
- フル解像度(2560×1984)は 1 フレーム 15MB。収録は mode 1(1920×1080)推奨。
- カメラ IP は電源断で揮発(set-ip はデーモンが再適用する方式)。プラグインの
  ワーカーが接続ごとに set-ip するので運用上の追加作業はない。
- リンク断が起きた時のエラーは 3 か所に現れる:
  connect 失敗(ConnectionError + stderr)、async_read の TimeoutError、
  ワーカー死亡(RuntimeError)。いずれも復旧は「カメラ冷却 + mgbe down/up」。
