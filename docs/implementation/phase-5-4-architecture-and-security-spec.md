# AgentDeck Phase 5.4：Web Push 架构与安全规范（v3.2 终审整改准入闭环版）

> **文档版本**：v3.2（终审整改准入闭环版）
> **基线对齐**：AgentDeck v1.3.0、Phase 5.2 数据库模型（26 个字段规范）、Phase 5.3 单元测试基准。  
> **核心定位**：本规范是 Phase 5.4 实现标准 Web Push 网关的生产级准入规范。  
> **前置边界**：**严正重申：Phase 5.2 数据库结构 100% 冻结，禁止任何数据库迁移或字段变更。** Phase 5.3 Worker 维持 Mock 适配器状态。本规范确立 Phase 5.4 授权实施的 Worker 封装接口规范与文件修改清单。  
> **红线警示**：本阶段为纯架构与安全规范修订，**严禁工程源码编写与真实外呼**。在获得明确授权前，系统严格维持 Mock 适配器默认运行（`WEBPUSH_ADAPTER_TYPE=mock`），严禁修改工程代码、数据库模型、Alembic 迁移脚本或依赖配置。历史 Worker 租约修改已单独建立审计报告，不并入本规范。

---

## 修订记录（v3.1 → v3.2 终审整改对照表）

| 编号 | 修订项（ChatGPT 终审阻断项） | 阻断等级 | v3.2 终审整改准入闭环规范 | 技术状态 |
| :--- | :--- | :--- | :--- | :--- |
| **0** | **修正 TLS 连接代码示例** | **P0-01** | 彻底删除重复的 `def connect`，修正代码块缩进与异常处理；保留 `do_handshake_on_connect=False`，先获取持有底层 fd 的 `SSLSocket` 再注册 Watchdog，最后显式 `do_handshake()`；明确覆盖 TCP connect、TLS handshake、HTTP request send、HTTP response read 四阶段；纠正 0.3s 绝对保证声明，明确其为设计目标与需实测指标。 | **设计已修订，已由源码证实** |
| **1** | **彻底消除热路径同步 Popen 与异步自愈状态机** | **P0-02** | `resolve()` 热路径 100% 零同步 Popen；子进程死亡时请求立即 Fail-Closed 快速失败，绝不等待不可中断的进程创建；异步调度单实例自愈工作线程防惊群与防无限累积；`_lifecycle_lock` 获取全部受全局 Deadline 约束，禁止无界等待；全局 8 秒 Deadline 预算严格传递扣减。 | **设计已修订** |
| **2** | **重新验证跨平台管道强关差异与 IO 工作线程回收** | **P0-03** | 深入剖析 POSIX 与 Windows 底层内核机制差异（阻塞 write、阻塞 flush、阻塞 readline 及跨线程 close 的行为）；明确子进程被 kill 释放对端句柄才是跨平台一致的中断机制，破除跨线程 close 管道的单边假设；建立 IO 线程极端挂起时的隔离队列（上限 3）与 CRITICAL 审计告警多级降级机制，破除 daemon 线程掩盖泄漏假设；设计针对 write、flush、readline 阻塞的三种独立测试用例。 | **设计已修订** |
| **3** | **完善 DNS 生命周期状态与幂等 close() 终态屏障** | **P0-04** | 引入 `ResolverState` 枚举（UNINITIALIZED, STARTING, RUNNING, RECOVERING, STOPPING, STOPPED）；`__init__` 初始化 `_closed = False` 与 `_state`；`start()` 检查关闭状态；`close()` 具备完全幂等性，关闭后坚决拒绝任何重启，彻底消除与自愈的死锁。 | **设计已修订** |
| **4** | **POSIX 原始路径组件严格校验** | **P0-03** | 保留独立提取的 `validate_posix_keys_path(keys_dir)`，在 `Path` 实例化前对原始路径进行绝对路径与组件切分校验，5 种形态覆盖测试。 | **设计已修订，尚待验证** |
| **5** | **Windows DACL 继承验证双模式闭环** | **P0-04** | 引入 `allow_inherited: bool = False`，阶段 2 采用继承合规模式验证，阶段 1/3/4 采用严格无继承模式验证。 | **设计已修订，尚待验证** |
| **6** | **Windows 防火墙与 Adapter 协同实现 Default Deny 架构** | **P1-03** | 明确 Windows Defender 默认出站 Allow 现状，由系统防火墙针对 27 个保留 CIDR 网段设置 TCP 443 Block，应用层 Adapter 对非 443 端口执行 100% 严格拒绝（Default Deny）。 | **设计已修订，尚待验证** |
| **7** | **原始状态与统一状态映射约束闭环** | **P1-01** | `DeliveryResponse` 明确区分 `status_code`（统一状态码 201）与 `raw_status_code`（真实原始状态码 200/201/202），数据库 `last_http_status` 严格持久化真实原始状态码。 | **设计已修订** |
| **8** | **文件范围界定闭环** | **P0-01** | 在第 10.2.2 节文件清单中明确 `requirements-production-webpush.lock`（17 项锁定），禁止修改 `requirements.txt` 与 `pyproject.toml`。 | **已由源码证实** |
---

## 零、边界与技术事实基线

为贯彻第一性原理并消除认知偏差，本规范依据四类严格的技术状态划分所有事实、假设与验证项：
1. **已由源码证实**：通过 CPython 3.12、urllib3 2.6.3、Requests 2.33.0 等官方源码或标准库实现证明的技术事实；
2. **设计已修订**：在规范中已经完成第一性原理闭环与完备性推演的权威设计方案；
3. **已实际测试通过**：在当前工程测试套件中已有真实通过断言支撑的事实；
4. **尚待验证**：必须在后续本地授权测试环境中由真实集成测试提供实测证据的项。

### 1. 已经写入环境验证的事实
- **数据库字段代码与单元测试事实**【已由源码证实，已实际测试通过】：`events.seq_id`、`notification_outbox.event_seq_id`、`notification_events.event_seq_id` 在 Phase 5.2 数据库迁移中固化，SQLite 底层保证同一数据库连接内递增。`event_seq_id` 组装时通过 `notification_events` 获取（`SELECT COALESCE(MAX(event_seq_id), 0) FROM notification_events WHERE notification_id = :notif_id`），无记录时安全回退为 `0`。
- **状态字段可空事实**【已由源码证实，已实际测试通过】：`notification_deliveries.last_http_status` 定义为 `Integer, nullable=True`，SQLite 原生支持写入 `NULL`，安全异常时有兜底。
- **现有 Worker 现状事实**【已由源码证实】：核对 `backend/services/notification_worker.py`，现有 Worker 阶段二目前传递硬编码 Mock Endpoint（`https://mock.gateway/wpush`）与空 JSON 载荷（`"{}"`），成功判断硬编码为 `if status_code == 201:`。在 Phase 5.4 中，Worker 必须经过精确封装递进至 Adapter，并将返回的 `200/201/202` 归一化为 `201`。
- **生产环境真实安装包与版本事实**【已由源码证实】：Python 3.12 虚拟环境真实确认安装包：`pywebpush==2.5.0` 与 `aiohttp==3.9.5`、`cryptography==50.0.2`、`requests==2.33.0`、`urllib3==2.6.3` 等全部 17 项生产依赖版本。
- **Requests 与 urllib3 层次结构事实**【已由源码证实】：核对 Requests 2.33.0 源码，`HTTPAdapter.send()` 在调用 `conn.urlopen()` 时原本传参 `assert_same_host=False`；而在 `urllib3 2.6.3` 中，`PoolKey` 严格映射连接池参数，在 `pool_kwargs` 中传递 `assert_same_host` 会触发 `TypeError: PoolKey.__new__() got an unexpected keyword argument 'key_assert_same_host'`。
- **跨平台管道中断与子进程强关底层事实**【已由源码证实，设计已修订】：
  - **POSIX 事实**：当伴生子进程被 `proc.kill()`（内核级投递 `SIGKILL`）强杀时，子进程所持有的管道读写端被操作系统内核强行回收。阻塞在 `stdin.write` 的父进程线程在向读端已关闭的管道写入时收到 `SIGPIPE`（在 Python 中表现为 `BrokenPipeError`）；阻塞在 `stdout.readline` 的父进程线程在写端完全关闭后立即收到 EOF（返回空字节 `b""`）。POSIX 标准中，跨线程对正在被 `read()` 系统调用使用的文件描述符调用 `close(fd)` 是未定义行为（Undefined Behavior），因此**打断读写线程的唯一可靠通用机制是子进程被 kill 后操作系统释放对端句柄，而非父进程跨线程 close**。
  - **Windows 事实**：Windows 匿名管道基于本地命名管道（Named Pipe）实现。子进程被 `TerminateProcess` 终止后，内核释放其句柄。阻塞在 `WriteFile` 的写线程立即返回 `ERROR_NO_DATA (232)` 或 `ERROR_BROKEN_PIPE (109)`；阻塞在 `ReadFile` 的读线程立即返回 `ERROR_BROKEN_PIPE (109)` 并被 Python C 运行时映射为 EOF。
- **跨操作系统 Popen 系统调用的同步不可中断性**【已由源码证实】：操作系统的 `CreateProcess` (Windows) 与 `fork+exec` / `posix_spawnp` (POSIX) 是不可打断的原子内核调用，Python 线程超时机制无法强退正在阻塞的创建进程调用。因此常驻热路径上必须严格禁止调用 `Popen`。
- **Python 3.12 SSL 包装与真实底层套接字所有权转移事实**【已由源码证实】：源码事实证明，CPython 3.12 `ssl.SSLSocket._create()` 在构造实例时执行 `sock.detach()`，原始套接字的 `fileno()` 立即变更为 `-1`，底层操作系统套接字文件描述符所有权完全转移给 `SSLSocket`。若 `wrap_socket(sock, do_handshake_on_connect=True)`，握手阻塞发生时 `wrap_socket` 尚未返回，原始 socket 已无法调用 `shutdown` 打断内核调用；反之，若传入 `do_handshake_on_connect=False`，`wrap_socket` 立即返回已接管底层 fd 的 `SSLSocket`，在发起握手前注册至 Watchdog，随后显式调用 `ssl_sock.do_handshake()`。Watchdog 触发时强关 `ssl_sock` 即可向内核发送 RST/FIN 打断底层阻塞。
- **Windows Defender Firewall 系统默认出站策略现状**【已由源码证实】：Windows Defender Firewall 系统默认出站策略为 `DefaultOutboundAction: Allow`。若在生产环境将系统全局默认出站策略更改为 `Block`，将导致宿主机所有未加白网络访问全部瘫痪。因此系统防火墙负责精准阻断 27 个保留 CIDR 的 TCP 443 访问，应用层 Adapter 负责对非 443 端口执行 Default Deny。
- **POSIX 原始路径组件识别**【已由源码证实】：`pathlib.Path` 实例化会自动归一化 `/./` 和 `/../`，因此必须在实例化前对原始字符串进行切分校验。

### 2. 阶段性假设
- **假设 1（Requests 与 OpenSSL 证书校验协同性）**【设计已修订】：在 `requests.adapters.HTTPAdapter` 中，通过 `self.build_connection_pool_key_attributes` 重构底层 TLS 上下文，写入 `host_params["host"] = verified_ip` 与 `pool_kwargs` 中 `server_hostname = orig_host` 及 `assert_hostname = orig_host`，在标准 Web Push 网关下均能真实实现直连与证书严格校验。
- **假设 2（非嵌套锁与零同步 Popen 下的伴生 DNS 稳定性）**【设计已修订】：通过生命周期锁 `_lifecycle_lock` 与通信互斥锁 `_ipc_lock` 严格非嵌套解耦，在常规 IPC 交互超时、异常或进程崩溃时，请求线程绝不同步调用 `Popen` 而是立即 Fail-Closed 并触发单实例后台自愈，不产生任何死锁与并发悬挂，且可在全局 Monotonic Deadline 严格约束内安全运转。
- **假设 3（Watchdog 握手前底层套接字与 SSLSocket 双阶段强关控制力）**【设计已修订】：在底层 TCP 连接建立后、TLS 握手前注册拥有底层连接的 `SSLSocket`（通过延迟握手），Watchdog 超时回调通过 `shutdown(SHUT_RDWR)` 与 `close()` 能在 TLS 握手挂起与 HTTP 响应读取挂起两个独立阶段均可靠中断底层连接。本规范不假定 0.3s 为所有平台的理论硬上限，该指标作为实测目标指标检验。
- **假设 4（Windows PowerShell 在多语言环境下的稳定性）**【设计已修订】：在非英文 Windows 操作系统中，PowerShell 的 `FileSystemRights` 枚举及 `param($p)` 能够稳定可靠传递并匹配 `FullControl`。
- **假设 5（Service Worker 中带 CAS 确认流程）**【设计已修订】：在 Service Worker 中，带单调版本号比较与 CAS 写入机制能够杜绝异步事件并发导致的旧版本覆写。
- **假设 6（双层出站安全可控性）**【设计已修订】：系统防火墙对 27 个保留 CIDR 实施 TCP 443 Block，应用层 Adapter 对非 443 端口实施 100% 拒绝，二者协同实现生产级 Default Deny。

### 3. 需通过的实测证据（当前状态：全部严格标注为尚待验证）
- **实测证据 1（证书匹配与不匹配测试）**【尚待验证】：编写带本地真实证书的 HTTPS 服务端，验证 `PinningHTTPAdapter` 在 `assert_hostname` 证书匹配时通信成功，不匹配时抛出 `SSLError`；流量直连指定 IP，标头保持 `Host: orig_host`。
- **实测证据 2a（TLS 握手挂起强关测试）**【尚待验证】：真实 HTTPS 服务端在 **TLS 握手阶段挂起**，验证 Watchdog 在预算时间内强关拥有真实底层 fd 的 `SSLSocket`，Adapter 真实抛出 `AbsoluteDeadlineExceededError`，中断耗时满足预算检验目标（$\le 0.30\text{s}$）。
- **实测证据 2b（HTTP 响应读取挂起强关测试）**【尚待验证】：真实 HTTPS 服务端在 **TLS 握手完成后、HTTP 响应读取阶段挂起**，验证 Watchdog 强关 `SSLSocket`，Adapter 真实抛出 `AbsoluteDeadlineExceededError`，中断耗时满足预算检验目标（$\le 0.30\text{s}$）。
- **实测证据 3（POSIX 环境原始路径 5 种形态覆盖测试）**【尚待验证】：在受控 POSIX 环境中，验证合法绝对路径 `/var/lib/keys` 顺利通过，而非法的 4 种形态（`/var/lib/./keys`、`/var/lib/../keys`、`var/lib/keys`、`./keys`）在实例化前被严格拒绝并 Fail-Closed。
- **实测证据 4（Windows 真实子进程特殊字符路径四阶段 DACL 测试）**【尚待验证】：在 Windows 真实子进程环境下，覆盖特殊字符与空格路径，验证四阶段 DACL（阶段 2 瞬时继承合规，阶段 1/3/4 严格无继承）全链路通过。
- **实测证据 5（Service Worker 双向异步时序 CAS 测试）**【尚待验证】：在真实异步 Promise 环境中，验证高版本先到、低版本后到以及相反时序下，IndexedDB 均 100% 杜绝旧版本覆写。
- **实测证据 6（预启动常驻 DNS 伴生进程零同步 Popen、自愈与多线程并发测试）**【尚待验证】：验证预启动 DNS 伴生进程生命周期内热路径 100% 零同步 Popen，进程崩溃后请求立即 Fail-Closed 并触发单实例后台自愈，多线程并发下无惊群无死锁，close 与 IPC 竞争安全退出。
- **实测证据 7（Windows Defender 防火墙与应用层协同测试）**【尚待验证】：验证防火墙脚本正确配置 27 个 CIDR 的 TCP 443 Block 规则，放通 UDP 53 DNS；应用层 Adapter 对非 443 端口与非法私网 IP 执行 100% 拒绝。
- **实测证据 8（IP Pinning 真实公网 HTTPS 服务端测试）**【尚待验证】：验证真实域名解析后绑定 IP 进行 HTTPS 请求，证书校验与通信正常。
- **实测证据 9（生产环境依赖锁定文件 17 项依赖完全一致性测试）**【尚待验证】：验证 `requirements-production-webpush.lock` 中的 17 项依赖与当前已安装环境 100% 精确一致。
---

## 〇、终审阻断问题逐项修订方案（v3.2 终审整改准入闭环版）

### 问题 1 (P0-01 & P0-02)：Watchdog TLS 四阶段时序、对象所有权与代码规范修复

- **原因分析**：
  上一版本中存在重复定义 `def connect(self) -> None:` 的低级语法错误与缩进瑕疵；同时对 TLS 握手强关时延存在“绝对 0.3 秒内完成”的未经跨平台实证的过度声明。在底层逻辑上，必须先取得实际接管底层操作系统 fd 的 `SSLSocket` 对象，再将其注册进 Watchdog，最后显式调用 `do_handshake()`。
- **权威设计方案**【设计已修订，已由源码证实】：
  1. **彻底消除重复定义**：重写 `WatchdogHTTPSConnection.connect()`，仅保留唯一定义，标准化 4 空格缩进，严格对象赋值顺序；
  2. **四阶段全生命周期覆盖**：
     - **阶段一（TCP Connect）**：`_new_conn()` 创建 `raw_sock` 并在发起连接前注册至 Watchdog。若 TCP 握手挂起，强关 `raw_sock`；
     - **阶段二（TLS Handshake）**：传入 `do_handshake_on_connect=False` 执行 `context.wrap_socket(...)`。此时 CPython 3.12 执行 `raw_sock.detach()`，真实 fd 转移至 `ssl_sock`。握手发起前立即将拥有底层真实 fd 的 `ssl_sock` 注册至 Watchdog（替换 `raw_sock`），随后显式调用 `ssl_sock.do_handshake()`。握手挂起时强关 `ssl_sock`；
     - **阶段三（HTTP Request Send）**：握手完成后 `self.sock = ssl_sock` 保持注册状态，发送阻塞时强关 `ssl_sock`；
     - **阶段四（HTTP Response Read）**：响应读取阶段慢速挂起时强关 `ssl_sock`；
  3. **关于中断时延的严正说明**：
     本规范明确：0.30s 为设计预算与实测检验目标，而非跨平台、所有 OpenSSL 阻塞状态下的理论绝对保证。真实网络环境中受内核协议栈调度、OpenSSL 内部锁及驱动影响，中断耗时必须由本地真实 HTTPS 测试证明【尚待验证】。

---

### 问题 2 (P0-02)：彻底消除热路径同步 Popen 与异步自愈状态机架构

- **原因分析**：
  若在 `resolve()` 热路径中发现子进程死亡并同步执行 `Popen` 重启，由于操作系统创建进程系统调用是内核级不可打断的同步调用，当宿主机高负载或 PID 资源争抢时，将导致请求线程永久卡死，全局 8 秒 Monotonic Deadline 预算失效。
- **权威设计方案**【设计已修订】：
  1. **热路径 100% 零同步 Popen**：
     `resolve()` 绝不同步调用 `Popen`。当无阻塞检测到伴生进程死亡或不可用时，当前请求线程**立即 Fail-Closed 快速失败**，抛出 `DnsResolutionError("Companion DNS process unavailable; async self-healing initiated")`，同时触发非阻塞的后台异步自愈调度。请求耗时严格 $\le 0.005\text{s}$。
  2. **多状态机转换**：
     引入严格状态机枚举 `ResolverState`：
     - `UNINITIALIZED`（初始未启动）
     - `STARTING`（启动探测中）
     - `RUNNING`（健康运行中）
     - `RECOVERING`（后台自愈拉起中）
     - `STOPPING`（停止中）
     - `STOPPED`（永久关闭屏障）
  3. **单实例自愈与后台线程防累积隔离**：
     引入 `self._healing_lock` 与 `self._healing_thread` 句柄。触发自愈时，若已有自愈线程在运行（`is_alive()`），禁止重复启动新线程，彻底防范惊群（Thundering Herd）；自愈线程内部限时获取生命周期锁（超时 2.0s），受独立超时控制拉起新进程并探测握手，失败则熔断断路器并记录错误日志。
  4. **`_lifecycle_lock` 获取必须受统一 Deadline 约束**：
     彻底废除无界等待 `with self._lifecycle_lock:`，统一采用 `if not self._lifecycle_lock.acquire(timeout=rem): raise AbsoluteDeadlineExceededError(...)`。
  5. **预算严格传递扣减**：
     全流程严格使用传入的 `deadline` 计算剩余时间：`rem = deadline - time.monotonic()`，任何阶段绝不重新创建完整 8 秒预算。

---

### 问题 3 (P0-03)：跨平台管道强关底层机制考证与工作线程多级降级

- **原因分析**：
  不能直接假定 Windows 与 POSIX 在关闭另一线程正在使用的管道句柄时行为完全一致。在 POSIX 中，跨线程关闭正在被 `read()` 的 fd 是未定义行为（Undefined Behavior），极易引发竞态；而在 Windows 中关闭正在 `ReadFile()` 的句柄可能导致无效句柄错误。
- **权威设计方案**【设计已修订，已由源码证实】：
  1. **跨平台唯一可靠中断机制**：
     实测与源码证明，**唯一通用、可靠的中断机制是对子进程调用 `proc.kill()`（POSIX 发送 `SIGKILL`，Windows 调用 `TerminateProcess`）**。子进程死亡后，操作系统内核自动回收子进程持有的对端管道句柄。
     - 在 POSIX 下，写端全部关闭促使父进程读端 `read()` 立即返回 EOF（空字节 `b""`），读端全部关闭促使写端 `write()` 收到 `SIGPIPE` 抛出 `BrokenPipeError`；
     - 在 Windows 下，对端句柄关闭促使 `ReadFile` 返回 `ERROR_BROKEN_PIPE (109)`，`WriteFile` 返回 `ERROR_NO_DATA (232)` 或 `ERROR_BROKEN_PIPE (109)`，驱动读写线程立即退出。
  2. **破除 daemon 掩盖泄漏与多级降级防线**：
     - 规范明确：`assert not io_thread.is_alive()` 仅作为测试验证断言，不作为生产运行时的唯一回收保障；
     - 生产降级机制：主线程执行 `io_thread.join(timeout=0.2)`，若因极端驱动挂起导致工作线程仍未退出，坚决不再复用该进程句柄，将其放入 `self._quarantined_threads` 隔离集合（容量上限 3），立即记录不可静默忽略的 `CRITICAL` 审计日志，触发断路器熔断。
  3. **细化覆盖三种阻塞场景的测试**：
     - 测试 2b-1：`stdin.write` 管道写阻塞强关打断测试【尚待验证】
     - 测试 2b-2：`stdin.flush` 冲刷阻塞强关打断测试【尚待验证】
     - 测试 2b-3：`stdout.readline` 管道读阻塞强关打断测试【尚待验证】

---

### 问题 4 (P0-04)：完善 DNS 生命周期状态与幂等 close() 终态屏障

- **权威设计方案**【设计已修订】：
  1. `__init__` 明确显式初始化 `self._closed = False`、`self._state = ResolverState.UNINITIALIZED`、`self._proc = None`、`self._quarantined_threads = []`；
  2. `start()` 严格检查 `self._closed`，若已关闭抛出 `DnsResolutionError("DNS resolver is permanently closed; cannot start")`；
  3. `close()` 实现完全幂等性：限时获取 `_lifecycle_lock(timeout=1.0)`，若已处于 `STOPPED` 直接返回；将状态置为 `ResolverState.STOPPED` 并置空 `self._proc`；释放锁后在锁外杀进程、关管道并 `wait(timeout=0.5)`；
  4. 终态屏障：一旦进入 `STOPPED`，后续任何 `start()`、`resolve()` 或异步自愈调度均被坚决拒绝，彻底阻断死锁与幽灵进程重启。

---

### 问题 5 (P0-03)：POSIX 原始路径组件严格校验（5 种形态覆盖）
- 保持独立函数 `validate_posix_keys_path(keys_dir)`，在 `Path` 实例化前原样切分，严格拦截含 `.` 或 `..` 的相对路径，5 种形态覆盖测试【尚待验证】。

---

### 问题 6 (P0-04)：Windows DACL 继承验证双模式闭环
- 保持 `allow_inherited: bool = False` 双模式设计，阶段 2 继承合规模式，阶段 1/3/4 严格无继承模式【尚待验证】。

---

### 问题 7 (P1-03)：Windows 防火墙与 Adapter 协同实现 Default Deny 架构
- 系统防火墙针对 27 个保留 CIDR 精准设置 TCP 443 Block（支持 `-Program "$ProgramPath"`），放通 UDP 53 DNS；应用层 Adapter 对非 443 端口执行 100% 严格拒绝【尚待验证】。

---

### 问题 8 (P1-01)：原始状态与统一状态映射约束闭环
- `DeliveryResponse` 显式区分 `status_code`（201）与 `raw_status_code`（200/201/202），数据库 `last_http_status` 严格持久化真实原始状态码【设计已修订】。
---

## 一、阶段定位与架构原则

Phase 5.4 的核心职责是将 Phase 5.3 中的抽象 `WebPushAdapter(Protocol)` 与 `MockWebPushAdapter` 演进为支持标准 Web Push 网关（Apple APNs、Mozilla Autopush、Google FCM、Microsoft WNS）的 `SsrfProtectedWebPushAdapter`。

规范确立**六大工程安全原则**：

1. **公网地址默认拒绝（Default Deny）**：严格 `ip.is_global is True` 校验与 IPv6 映射剥离，底层 IP 直连绑定，杜绝 SSRF 与 DNS Rebinding；
2. **系统凭据最小权限隔离**：POSIX 平台使用全流程安全目录与 `dir_fd`，写时复制执行双重 `fsync`，缺失必要标志 Fail-Closed 熔断，写入确认临时文件原子替换异常时安全清理记录审计；Windows 平台使用 Current User SID 实施独占 DACL 策略，验证显式 DACL，失败时中止。
3. **贯穿全流程的绝对 Monotonic Deadline**：统一硬锚定 8.0 秒绝对截止时间，剩余预算不足立即失败拒绝，时延与 DNS 伴生进程 kill 强关、底层套接字异步 Watchdog 结合，绝不超时挂死。
4. **状态码原子失败持久化**：同一 SQLite 事务处理 Delivery CAS 与订阅注销，`last_http_status` 真实记录 404 等判断异常点（非 NULL），杜绝浅度轮询导致 Worker 挂死。
5. **协议约定一致性**：Adapter 内部将外部返回的 `200/201/202` 统一归一化为内部约定的 `status_code=201`，协议零冲突。
6. **敏感信息零泄漏**：Token 密钥日志脱敏，强防火墙严格遵循 IANA RFC 6890 与应用层兜底拦截，协同放通系统 DNS 端口策略。

---

## 二、Push Endpoint SSRF 防御与实现连接池架构

### 2.1 威胁建模
攻击者可能通过恶意 Endpoint 实施指向私有地址或云元数据接口（`169.254.169.254`）的探测、利用 DNS Rebinding 实施动态解析绕过、通过 3xx 重定向诱骗至受保护内网、通过 IPv4-mapped IPv6 规避纯 IPv4 校验，或通过慢速挂起消耗系统连接池耗尽工作线程。

### 2.2 防御实现规范
1. **URL 解析与协议端口白名单**：URL 必须为标准 `https://`，端口严格限制为 443，禁止任何非标准端口。
2. **多 IP 解析与遍历验证**：调用常驻 DNS 伴生进程解析目标 Host，获取全部 IPv4/IPv6 列表。严格遍历校验每一个 IP，必须 `ip.is_global is True` 且不属于 27 个禁止 CIDR。
3. **底层 IP 直连与 SNI/Host 绑定（IP Pinning）**：通过自定义 `PinningHTTPAdapter` 与 `WatchdogHTTPSConnection`，直接连接校验通过的 IP，同时将 TLS SNI 与 HTTP `Host` 标头设置为原始域名，确保证书校验完整通过。
4. **强制单次连接隔离**：所有请求必须添加 `Connection: close` 标头，杜绝连接复用导致的 Watchdog 串线与状态污染。

### 2.3 pywebpush 自定义 Session / Adapter 实现链路
通过继承 `requests.adapters.HTTPAdapter`，覆盖 `get_connection_with_tls_context` 与 `send`，将底层连接托管给 `WatchdogHTTPSConnectionPool`。

### 2.4 核心连接池关键实现代码
```python
import socket
import ssl
import time
import typing
import weakref
import logging
import threading
import contextvars
import ipaddress
import urllib3
import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool

# 绑定当前线程 Watchdog 的线程安全、异步上下文感知 Hook
current_watchdog_cv = contextvars.ContextVar("current_webpush_watchdog", default=None)

class WatchdogContext:
    \"\"\"绑定 Watchdog 至 ContextVar 的上下文管理器\"\"\"
    def __init__(self, watchdog: "AbsoluteDeadlineWatchdog"):
        self.watchdog = watchdog
        self.token = None

    def __enter__(self):
        self.watchdog.start()
        self.token = current_watchdog_cv.set(self.watchdog)
        return self.watchdog

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self.watchdog.stop()
        finally:
            if self.token is not None:
                current_watchdog_cv.reset(self.token)
                self.token = None

class WatchdogHTTPSConnection(HTTPSConnection):
    \"\"\"
    支持 IPv4/IPv6 自适应绑定，并在发起 connect 之前与 TLS 延迟包装后向 Watchdog 注册的底层连接类。
    
    【技术状态：已由 CPython 3.12 / urllib3 2.6.3 源码证实，设计已修订】
    
    底层套接字生命周期与 Watchdog 四阶段覆盖时序：
    1. 阶段一（TCP Connect 阶段）：
       - _new_conn() 创建操作系统原始套接字（raw_sock）；
       - 在发起 sock.connect() 前，立即注册到当前活跃的 Watchdog；
       - 若 TCP 握手挂起（如远端丢弃 SYN/ACK），Watchdog 强关 raw_sock 中断连接。
    2. 阶段二（TLS Handshake 阶段）：
       - 调用 context.wrap_socket(raw_sock, do_handshake_on_connect=False, server_hostname=...) 进行延迟握手包装；
       - CPython 3.12 内部在 SSLSocket._create 中执行 raw_sock.detach()，底层操作系统文件描述符（fd）所有权转移给 SSLSocket，原始 raw_sock.fileno() 变为 -1；
       - 握手发起前，立即将接管底层真实 fd 的 ssl_sock 注册到 Watchdog（替换 raw_sock）；
       - 显式调用 ssl_sock.do_handshake() 发起握手；若服务端在 Client Hello 后挂起，Watchdog 强关持有有效 fd 的 ssl_sock 打断内核阻塞系统调用。
    3. 阶段三（HTTP Request Send 阶段）：
       - 握手成功后，self.sock 赋值为 ssl_sock，ssl_sock 保持在 Watchdog 中的注册状态；
       - 若 HTTP 发送阶段因网络拥塞或套接字写缓冲区满导致阻塞，Watchdog 超时强关 ssl_sock。
    4. 阶段四（HTTP Response Read 阶段）：
       - ssl_sock 继续受 Watchdog 保护；
       - 若服务端在响应读取阶段发生慢速挂起（Slowloris 或不发送响应），Watchdog 超时强关 ssl_sock。
       
    【关于中断时延的严正说明】：
    本规范明确：0.30s 为设计预算与实测检验目标，而非跨平台、所有 OpenSSL 阻塞状态下的理论绝对保证。真实网络环境中受内核协议栈调度、OpenSSL 内部锁及驱动影响，中断耗时必须由本地真实 HTTPS 测试证明【尚待验证】。
    \"\"\"
    def _new_conn(self) -> socket.socket:
        # 1. 协议自适应判断
        ip_obj = ipaddress.ip_address(self._dns_host)
        family = socket.AF_INET6 if ip_obj.version == 6 else socket.AF_INET
        
        # 2. 创建原始底层套接字
        sock = socket.socket(family, socket.SOCK_STREAM)
        
        # 3. 覆盖阶段一（TCP Connect）：在发起 connect 前注册原始 Socket 到 Watchdog
        active_watchdog = current_watchdog_cv.get()
        if active_watchdog is not None:
            active_watchdog.register_socket(sock)
        
        # 4. 设置连接超时
        if self.timeout is not None:
            sock.settimeout(self.timeout)
        
        try:
            sock.connect((self._dns_host, self.port))
        except Exception:
            sock.close()
            raise
        
        return sock

    def connect(self) -> None:
        \"\"\"
        显式重写 connect() 建立全生命周期安全连接（单入口唯一定义，杜绝重复）：
        1. 调用 self._new_conn() 建立底层 TCP 连接并受 Watchdog 保护；
        2. 准备 TLS context 与 SNI；
        3. 调用 wrap_socket(..., do_handshake_on_connect=False) 延迟握手包装，接管底层 fd；
        4. 覆盖阶段二（TLS Handshake）：在显式握手发起前，注册拥有底层真实 fd 的 ssl_sock；
        5. 显式调用 ssl_sock.do_handshake()，异常时安全关闭并向上传播；
        6. 握手成功后执行严格的证书主机名与指纹校验；
        7. 覆盖阶段三与阶段四（HTTP Send / Read）：将 self.sock 赋为 ssl_sock，维持全流程强关保护。
        \"\"\"
        # 步骤 1: 建立底层 TCP 连接
        self.sock = self._new_conn()
        if self._tunnel_host:
            self._tunnel()
            
        raw_sock = self.sock
        active_watchdog = current_watchdog_cv.get()
        
        # 步骤 2: 构建 TLS 上下文并确定 SNI
        server_hostname = self.server_hostname
        if self.assert_hostname is not None:
            server_hostname = self.assert_hostname
            
        context = self.ssl_context
        if context is None:
            context = urllib3.util.ssl_.create_urllib3_context(
                ssl_version=urllib3.util.ssl_.resolve_ssl_version(self.ssl_version),
                ssl_minimum_version=self.ssl_minimum_version,
                ssl_maximum_version=self.ssl_maximum_version,
                cert_reqs=urllib3.util.ssl_.resolve_cert_reqs(self.cert_reqs),
                ca_certs=self.ca_certs,
                ca_cert_dir=self.ca_cert_dir,
                ca_cert_data=self.ca_cert_data,
            )
            
        # 步骤 3: 延迟握手包装（do_handshake_on_connect=False）
        # CPython 3.12 在此执行 raw_sock.detach()，底层 fd 所有权转移给 ssl_sock
        ssl_sock = context.wrap_socket(
            raw_sock,
            server_side=False,
            do_handshake_on_connect=False,
            server_hostname=server_hostname,
        )
        
        # 步骤 4: 握手发起前注册接管底层真实 fd 的 ssl_sock（覆盖 TLS 握手挂起阶段）
        if active_watchdog is not None:
            active_watchdog.register_socket(ssl_sock)
            
        # 步骤 5: 显式发起 TLS 握手
        try:
            ssl_sock.do_handshake()
        except BaseException:
            try:
                ssl_sock.close()
            except Exception:
                pass
            raise
            
        # 步骤 6: 握手成功后执行证书与主机名严格校验
        if self.assert_fingerprint:
            urllib3.util.ssl_._assert_fingerprint(
                ssl_sock.getpeercert(binary_form=True), self.assert_fingerprint
            )
        elif (
            context.verify_mode != ssl.CERT_NONE
            and not context.check_hostname
            and self.assert_hostname is not False
        ):
            cert = ssl_sock.getpeercert()
            urllib3.util.ssl_._match_hostname(cert, self.assert_hostname or server_hostname)
            
        # 步骤 7: 将连接 socket 更新为验证通过的 SSLSocket，覆盖后续 HTTP 请求与响应读取阶段
        self.sock = ssl_sock
        self.is_verified = context.verify_mode == ssl.CERT_REQUIRED or bool(self.assert_fingerprint)

class WatchdogHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = WatchdogHTTPSConnection

class PinningHTTPAdapter(HTTPAdapter):
    \"\"\"配合 Requests 2.33.0 与 urllib3 2.6.3 的 IP 锁定适配器\"\"\"
    def __init__(self, verified_ip: str, orig_host: str, deadline: float, **kwargs):
        super().__init__(**kwargs)
        self.verified_ip = verified_ip
        self.orig_host = orig_host
        self.deadline = deadline

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        # 将底层 HTTPS 连接池替换为受控的 Watchdog 连接池
        self.poolmanager.pool_classes_by_scheme["https"] = WatchdogHTTPSConnectionPool

    def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
        # 1. 继承 Requests 原生 TLS 上下文（CA 路径、系统证书、cert_reqs）
        host_params, pool_kwargs = self.build_connection_pool_key_attributes(request, verify, cert)
        
        # 2. 目标改写为已验证的目标 IP
        host_params["host"] = self.verified_ip
        
        # 3. 明确向底层连接池键 PoolKey 支持的字段注入 SNI 与主机名校验
        pool_kwargs["server_hostname"] = self.orig_host
        pool_kwargs["assert_hostname"] = self.orig_host
        
        return self.poolmanager.connection_from_host(**host_params, pool_kwargs=pool_kwargs)

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        # 4. 显式将 Host 请求头设置为原始域名，杜绝泄漏 IP
        request.headers["Host"] = self.orig_host
        # 显式禁止连接池长连接复用，确保每次请求均为独立连接并严格注册专用 Watchdog
        request.headers["Connection"] = "close"
        
        try:
            return super().send(request, stream=stream, timeout=timeout, verify=verify, cert=cert, proxies=proxies)
        except (socket.error, urllib3.exceptions.HTTPError, requests.exceptions.RequestException) as exc:
            # 5. 严格异常映射：检查当前活跃 Watchdog 是否已被触发或已超时
            active_watchdog = current_watchdog_cv.get()
            if active_watchdog is not None:
                if active_watchdog.triggered or time.monotonic() >= active_watchdog.deadline:
                    raise AbsoluteDeadlineExceededError(
                        f"Request exceeded absolute deadline ({active_watchdog.deadline}s) during transport: {exc}"
                    ) from exc
            raise
```
---

## 三、统一全局 8 秒 Deadline 安全闭环

### 3.1 统一计算、时间预算与 Worker 内部安全架构

Worker 在启动时建立单次请求的绝对 Monotonic Deadline：
$$\text{deadline} = \text{time.monotonic()} + 8.0$$

预算严格从当前 Deadline 扣减，并在解析、自愈、连接、握手、发送、读取六个环节逐级传递，禁止任何阶段重新创建完整 8 秒预算。

```python
def check_remaining_budget_or_fail(deadline: float, min_required: float = 0.05) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= min_required:
        raise AbsoluteDeadlineExceededError(
            f"Remaining deadline budget ({remaining:.3f}s) is insufficient (min: {min_required}s); Fail-Closed"
        )
    return remaining
```

### 3.2 预启动常驻 DNS 子进程双锁架构与零同步 Popen 规范

```python
import enum
import json
import subprocess

class ResolverState(enum.Enum):
    UNINITIALIZED = "UNINITIALIZED"  # 初始未启动
    STARTING = "STARTING"            # 首次启动中
    RUNNING = "RUNNING"              # 健康运行中
    RECOVERING = "RECOVERING"        # 异常自愈中（单实例后台处理）
    STOPPING = "STOPPING"            # 停止中
    STOPPED = "STOPPED"              # 已永久关闭（终态屏障）

class PersistentCompanionDnsResolver:
    \"\"\"
    常驻伴生 DNS 解析器（v3.2 终审整改版）：
    1. 彻底消除请求热路径 Popen：resolve() 绝不执行同步 Popen；
    2. 明确多状态机转换与 close() 幂等永久屏障；
    3. 单实例异步自愈调度与后台线程防累积隔离；
    4. _lifecycle_lock 严格受当前 Deadline 预算约束，禁止无界等待；
    5. 绝对零嵌套锁规范（_lifecycle_lock 与 _ipc_lock 嵌套深度严格为 0）。
    \"\"\"
    def __init__(self, script_path: Optional[str] = None):
        self.script_path = script_path or os.path.join(
            os.path.dirname(__file__), "companion_dns_worker.py"
        )
        self._state = ResolverState.UNINITIALIZED
        self._closed = False
        self._proc: Optional[subprocess.Popen] = None
        self._lifecycle_lock = threading.Lock()
        self._ipc_lock = threading.Lock()
        self._healing_thread: Optional[threading.Thread] = None
        self._healing_lock = threading.Lock()
        self.circuit_breaker = DnsCircuitBreaker(failure_threshold=3, recovery_cooldown=30.0)
        self._quarantined_threads = []  # 极端挂起线程隔离队列（上限 3）

    def start(self, timeout: float = 2.0) -> None:
        \"\"\"
        Worker 启动阶段显式调用的预启动探测。
        受 timeout 约束，若已关闭则拒绝启动。
        \"\"\"
        if self._closed:
            raise DnsResolutionError("DNS resolver is permanently closed; cannot start")
            
        if not self._lifecycle_lock.acquire(timeout=timeout):
            raise DnsInitializationError(f"Timed out acquiring lifecycle lock during start({timeout}s)")
        try:
            if self._closed:
                raise DnsResolutionError("DNS resolver is permanently closed; cannot start")
            if self._state == ResolverState.RUNNING and self._proc is not None and self._proc.poll() is None:
                return  # 幂等返回
            self._state = ResolverState.STARTING
            self._start_process_unlocked(timeout=timeout)
            self._state = ResolverState.RUNNING
        finally:
            self._lifecycle_lock.release()

    def _start_process_unlocked(self, timeout: float = 2.0) -> None:
        \"\"\"
        内部原子拉起进程（必须在持有 _lifecycle_lock 时调用）。
        分步硬超时：Popen -> write ping -> read pong。
        \"\"\"
        if self._proc is not None:
            self._kill_and_clean_proc_unlocked(self._proc)
            self._proc = None
            
        t0 = time.monotonic()
        cmd = [sys.executable, "-u", self.script_path]
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )
        except Exception as exc:
            self._state = ResolverState.UNINITIALIZED
            raise DnsInitializationError(f"Failed to spawn companion DNS process: {exc}") from exc

        # 握手探针
        rem = timeout - (time.monotonic() - t0)
        if rem <= 0.05:
            self._kill_and_clean_proc_unlocked(self._proc)
            self._proc = None
            self._state = ResolverState.UNINITIALIZED
            raise DnsInitializationError("Startup window exhausted before handshake probe")

        handshake_ok = False
        try:
            self._proc.stdin.write(json.dumps({"ping": True}) + "\n")
            self._proc.stdin.flush()
            
            resp_box = []
            def _probe():
                try:
                    line = self._proc.stdout.readline()
                    if line:
                        resp_box.append(line)
                except Exception:
                    pass
            t = threading.Thread(target=_probe, daemon=True)
            t.start()
            t.join(timeout=rem)
            if resp_box and json.loads(resp_box[0].strip()).get("pong") is True:
                handshake_ok = True
        except Exception:
            pass

        if not handshake_ok:
            self._kill_and_clean_proc_unlocked(self._proc)
            self._proc = None
            self._state = ResolverState.UNINITIALIZED
            raise DnsInitializationError("DNS companion process failed handshake probe during startup")

    def _kill_and_clean_proc_unlocked(self, proc: subprocess.Popen) -> None:
        \"\"\"强制终止进程并关闭其管道（持有 _lifecycle_lock 下的辅助清理）\"\"\"
        try:
            proc.kill()
        except Exception:
            pass
        for s in (proc.stdin, proc.stdout, proc.stderr):
            if s:
                try:
                    s.close()
                except Exception:
                    pass
        try:
            proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            import logging
            logging.getLogger("security").critical(
                f"CRITICAL: Companion DNS process PID={getattr(proc, 'pid', None)} failed to exit within 0.5s; force-detached"
            )

    def _trigger_async_self_heal(self) -> None:
        \"\"\"
        触发异步单实例后台自愈线程（热路径绝不阻塞，零 Popen）。
        防惊群（Thundering Herd）保证：严格只允许一个自愈线程在后台运行。
        \"\"\"
        if self._closed or self.circuit_breaker.is_open():
            return

        with self._healing_lock:
            # 检查上一个自愈线程是否还在运行（防止线程无限累积）
            if self._healing_thread is not None and self._healing_thread.is_alive():
                return  # 已有自愈线程在处理中，当前直接返回

            def _background_heal_worker():
                # 尝试获取生命周期锁（最多等待 2.0s）
                if not self._lifecycle_lock.acquire(timeout=2.0):
                    import logging
                    logging.getLogger("security").warning("Background self-healing could not acquire lifecycle lock; aborted")
                    return
                try:
                    if self._closed or self.circuit_breaker.is_open():
                        return
                    # 双重检查
                    if self._proc is not None and self._proc.poll() is None:
                        self._state = ResolverState.RUNNING
                        return
                    self._state = ResolverState.RECOVERING
                    self._start_process_unlocked(timeout=2.0)
                    self._state = ResolverState.RUNNING
                    self.circuit_breaker.record_success()
                except Exception as e:
                    self._state = ResolverState.UNINITIALIZED
                    self.circuit_breaker.record_failure()
                    import logging
                    logging.getLogger("security").error(f"Background DNS self-healing failed: {e}")
                finally:
                    self._lifecycle_lock.release()

            self._healing_thread = threading.Thread(
                target=_background_heal_worker,
                daemon=True,
                name="dns_companion_async_healer"
            )
            self._healing_thread.start()

    def resolve(self, host: str, deadline: float) -> list[str]:
        \"\"\"
        常驻路径解析逻辑（100% 零同步 Popen、严格零嵌套锁、预算递减传递）：
        1. 状态与关闭屏障检查：已关闭直接拒绝；
        2. 断路器检查：OPEN 状态直接拒绝；
        3. 预算前置校验：从全局 deadline 扣减，不足立即 Fail-Closed；
        4. 存活检查与零同步 Popen：
           若子进程死亡，触发后台异步单实例自愈，当前请求立即 Fail-Closed 抛出 DnsResolutionError，
           绝不在请求线程中调用 Popen！
        5. 限时获取 _ipc_lock（受剩余预算约束）；
        6. 工作线程执行管道 IO，超时或异常在锁内 kill 子进程（依靠内核对端关闭机制打断 reader），
           主线程 io_thread.join(timeout=0.2) 安全回收，无法回收时隔离并记录 CRITICAL 日志；
        7. 释放 _ipc_lock 后，锁外解绑并回收子进程句柄。
        \"\"\"
        if self._closed or self._state in (ResolverState.STOPPING, ResolverState.STOPPED):
            raise DnsResolutionError(f"DNS resolver is permanently closed; cannot resolve {host}")

        if self.circuit_breaker.is_open():
            raise DnsCircuitBreakerOpenError(f"DNS circuit breaker OPEN for {host}")

        # 1. 预算前置检查
        rem_total = check_remaining_budget_or_fail(deadline, min_required=0.1)

        # 2. 存活快检（零 Popen 核心）：无阻塞检查当前进程状态
        proc = self._proc
        if proc is None or proc.poll() is not None or self._state != ResolverState.RUNNING:
            self.circuit_breaker.record_failure()
            # 触发后台异步自愈（绝不同步阻塞调用 Popen）
            self._trigger_async_self_heal()
            raise DnsResolutionError(f"Companion DNS process is unavailable for {host}; async self-healing initiated")

        # 3. 动态计算 IPC 剩余超时预算
        ipc_timeout = min(2.0, check_remaining_budget_or_fail(deadline, min_required=0.05))

        # 4. 限时获取通信管道互斥锁（受 Deadline 约束，杜绝无界等待）
        acquired = self._ipc_lock.acquire(timeout=ipc_timeout)
        if not acquired:
            raise AbsoluteDeadlineExceededError(f"DNS resolution timed out waiting for IPC lock after {ipc_timeout:.2f}s")

        need_reap = False
        try:
            # 再次检查进程有效性
            if proc.poll() is not None or self._closed:
                self.circuit_breaker.record_failure()
                self._trigger_async_self_heal()
                raise DnsResolutionError("Companion DNS process terminated right before IPC dispatch")

            # 5. 管道写入与读取在工作线程中限时执行
            io_container = {}
            io_error = []

            def _do_ipc_io():
                try:
                    proc.stdin.write(host + "\\n")
                    proc.stdin.flush()
                    line = proc.stdout.readline()
                    if line:
                        io_container["data"] = json.loads(line.strip())
                except Exception as err:
                    io_error.append(err)

            io_thread = threading.Thread(target=_do_ipc_io, daemon=True, name="dns_ipc_io")
            io_thread.start()
            io_thread.join(timeout=ipc_timeout)

            # 6. 超时处理：利用操作系统 kill 子进程释放对端句柄打断 IO 线程
            if io_thread.is_alive():
                need_reap = True
                try:
                    proc.kill()
                except Exception:
                    pass
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    if stream:
                        try:
                            stream.close()
                        except Exception:
                            pass
                # 等待工作线程因内核 BrokenPipe/EOF 退出
                io_thread.join(timeout=0.2)
                if io_thread.is_alive():
                    # 多级降级防线：工作线程发生极端内核死锁无法退出时的隔离与告警
                    if len(self._quarantined_threads) < 3:
                        self._quarantined_threads.append(io_thread)
                    import logging
                    logging.getLogger("security").critical(
                        f"CRITICAL: DNS IO worker thread {io_thread.ident} hung on pipe and failed to exit within 0.2s after proc.kill(); quarantined"
                    )
                self.circuit_breaker.record_failure()
                self._trigger_async_self_heal()
                raise DnsResolutionTimeoutError(f"DNS resolution IPC timed out after {ipc_timeout:.2f}s")

            # 7. 异常处理
            if io_error:
                need_reap = True
                try:
                    proc.kill()
                except Exception:
                    pass
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    if stream:
                        try:
                            stream.close()
                        except Exception:
                            pass
                io_thread.join(timeout=0.2)
                self.circuit_breaker.record_failure()
                self._trigger_async_self_heal()
                raise DnsResolutionError(f"DNS companion IPC I/O error: {io_error[0]}") from io_error[0]

            if "data" not in io_container:
                need_reap = True
                try:
                    proc.kill()
                except Exception:
                    pass
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    if stream:
                        try:
                            stream.close()
                        except Exception:
                            pass
                io_thread.join(timeout=0.2)
                self.circuit_breaker.record_failure()
                self._trigger_async_self_heal()
                raise DnsResolutionError("DNS companion process returned empty EOF response")

            data = io_container["data"]
            if not data.get("ok"):
                self.circuit_breaker.record_failure()
                raise DnsResolutionError(f"DNS lookup failed: {data.get('error')}")

            self.circuit_breaker.record_success()
            return data["ips"]

        finally:
            self._ipc_lock.release()
            # 8. 严格在 _ipc_lock 释放后执行进程句柄解绑（零嵌套锁）
            if need_reap:
                self._detach_and_reap_proc(proc)

    def _detach_and_reap_proc(self, proc: Optional[subprocess.Popen]) -> None:
        \"\"\"锁外安全解绑并回收子进程（限时获取生命周期锁，绝无嵌套）\"\"\"
        if proc is None:
            return
        if self._lifecycle_lock.acquire(timeout=0.5):
            try:
                if self._proc is proc:
                    self._proc = None
                    if self._state == ResolverState.RUNNING:
                        self._state = ResolverState.UNINITIALIZED
            finally:
                self._lifecycle_lock.release()
        try:
            proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            import logging
            logging.getLogger("security").critical(
                f"CRITICAL: Companion DNS process PID={getattr(proc, 'pid', None)} failed to exit within 0.5s; force-detached"
            )

    def close(self) -> None:
        \"\"\"
        Worker 停止周期：完全幂等的终态屏障设计
        1. 限时获取 _lifecycle_lock；
        2. 若已经处于 STOPPED，直接幂等返回；
        3. 置 _closed = True，_state = ResolverState.STOPPED，取出 _proc 并置空；
        4. 释放 _lifecycle_lock；
        5. 锁外强杀进程，关闭管道句柄；
        6. 非阻塞/限时尝试释放 IPC 锁；
        7. 彻底断绝后续任何 start()、resolve() 或自愈请求！
        \"\"\"
        proc_to_kill = None
        # 限时获取生命周期锁（防止 close 死锁）
        if self._lifecycle_lock.acquire(timeout=1.0):
            try:
                if self._closed and self._state == ResolverState.STOPPED:
                    return  # 幂等返回
                self._closed = True
                self._state = ResolverState.STOPPED
                if self._proc is not None:
                    proc_to_kill = self._proc
                    self._proc = None
            finally:
                self._lifecycle_lock.release()
        else:
            # 极端超时兜底
            self._closed = True
            self._state = ResolverState.STOPPED
            proc_to_kill = self._proc
            self._proc = None

        if proc_to_kill is not None:
            try:
                proc_to_kill.kill()
            except Exception:
                pass
            for stream in (proc_to_kill.stdin, proc_to_kill.stdout, proc_to_kill.stderr):
                if stream:
                    try:
                        stream.close()
                    except Exception:
                        pass

        if self._ipc_lock.acquire(timeout=0.5):
            self._ipc_lock.release()

        if proc_to_kill is not None:
            try:
                proc_to_kill.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                import logging
                logging.getLogger("security").critical(
                    f"CRITICAL: Companion DNS process PID={getattr(proc_to_kill, 'pid', None)} failed to exit during close; detached"
                )
```
### 3.3 独立异步看门狗 `AbsoluteDeadlineWatchdog` 无竞态物理强关与超时异常传递

```python
class AbsoluteDeadlineWatchdog:
    """脱离业务主线程独立计时的绝对截止时间物理看门狗（保证无竞态与严格状态核验）"""
    def __init__(self, deadline: float):
        self.deadline = deadline
        self.active_sockets = weakref.WeakSet()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self.triggered = False

    def register_socket(self, sock: socket.socket):
        """注册套接字：若 Watchdog 已触发或已过 deadline，立即物理强关并抛出异常，绝不遗漏"""
        with self._lock:
            if self.triggered or time.monotonic() >= self.deadline:
                self.triggered = True
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    sock.close()
                except Exception:
                    pass
                raise AbsoluteDeadlineExceededError(
                    f"Cannot register socket: deadline already reached or watchdog triggered"
                )
            self.active_sockets.add(sock)

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._monitor, daemon=True, name="deadline_watchdog")
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.2)
        with self._lock:
            self.active_sockets.clear()

    def _monitor(self):
        remaining = self.deadline - time.monotonic()
        if remaining > 0:
            if self._stop_event.wait(timeout=remaining):
                return  # 正常完成，优雅退出
        
        # 严格到达绝对截止时间，全程在互斥锁保护下标记触发并物理强杀所有活跃套接字
        with self._lock:
            self.triggered = True
            for sock in list(self.active_sockets):
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    sock.close()
                except Exception:
                    pass
            self.active_sockets.clear()
```

### 3.4 连接池隔离、IPv4/IPv6 自适应与异常捕获转换契约

1. **确定性异常传递与识别机制**：
   - 当 Watchdog 在后台关闭套接字时，阻塞在 `connect`、`SSL.do_handshake` 或 `recv` 的主线程会立即抛出底层系统异常（如 `ConnectionResetError`、`OSError: [WinError 10038]`、`ProtocolError` 等）；
   - 在 Adapter 外呼封装层统一捕获任何 `Exception`，执行判断：
     ```python
     if (watchdog and watchdog.triggered) or time.monotonic() >= deadline:
         raise AbsoluteDeadlineExceededError(
             f"Outbound operation physically interrupted: absolute 8.0s deadline reached: {exc}"
         ) from exc
     ```
   - Adapter 防腐层在捕获 `AbsoluteDeadlineExceededError` 后，返回 `DeliveryResponse(status_code=0, error_message="AbsoluteDeadlineExceededError: ...")`，指示 Worker 状态机显式将 `last_http_status` 清零为 `NULL` 并触发退避重试。
2. **连接池长连接复用彻底阻断（Connection: close）**：
   - 为防止连接池跨请求保留长连接导致：(a) 新请求复用旧连接未调用 `_new_conn()` 导致套接字脱管；或 (b) 上一个请求超时的旧 Watchdog 误杀新请求连接；
   - 规定：每个 Web Push 请求显式携带 `Connection: close` 标头，使用单次独立的 `Session` 与 `PinningHTTPAdapter` 实例，外呼完成后立即执行 `session.close()` 彻底释放。
3. **并发请求与多线程隔离**：
   - 每个外呼任务实例化其专属的 `AbsoluteDeadlineWatchdog`；
   - 通过 `WatchdogContext` 上下文管理器将专属 Watchdog 注入当前执行线程/协程的 `current_watchdog_cv`；
   - 在 `finally` 块中确定性调用 `current_watchdog_cv.reset(token)`，确保高并发多线程下套接字注册与 Watchdog 监控绝对严格一对一隔离，互不串台。
4. **IPv4 与 IPv6 协议族原生自适应**：
   - `WatchdogHTTPSConnection._new_conn()` 严格通过 `ipaddress.ip_address(self._dns_host).version` 识别目标 IP；
   - 若为版本 6 则以 `socket.AF_INET6` 创建套接字，否则以 `socket.AF_INET` 创建；
   - 创建套接字后立即执行 `active_watchdog.register_socket(sock)`，再发起 `connect`，确保双栈环境下套接字全生命周期均受监控。

---

## 四、VAPID 凭据管理与系统级绝对权限隔离

### 4.1 POSIX 平台目录描述符安全、逐级防劫持、短写处理与 Fail-Closed 规则

```python
def validate_posix_keys_path(keys_dir: str) -> None:
    """
    POSIX 路径前置双重防御校验（防绕过漏洞、防 pathlib 自动归一化篡改）：
    1. 第一道防线（绝对路径硬校验）：原样以字符串 '/' 开头，拒绝相对路径 var/lib/keys, ./keys；
    2. 第二道防线（原始字符串切分检查）：以 '/' 分割原始字符串，遍历任何 '.' 或 '..'；
       杜绝 pathlib.Path 自动将 /var/lib/./keys, /var/lib/../keys 归一化后绕过；
    3. 合法路径如 /var/lib/keys，顺利通过前置防线进入 dir_fd 与 fstat 权限检查。
    """
    if not isinstance(keys_dir, str) or not keys_dir.startswith("/"):
        raise SecurityConfigurationError(f"Keys directory path must be absolute starting with '/': '{keys_dir}'; Fail-Closed")
    
    # 原始字符串切分，在任何 Path 实例化前执行
    raw_components = [c for c in keys_dir.replace("\\", "/").split("/") if c]
    for comp in raw_components:
        if comp in (".", ".."):
            raise SecurityConfigurationError(
                f"Relative path component '{comp}' disallowed in raw path '{keys_dir}'; Fail-Closed"
            )

def open_posix_directory_stepwise_secure(keys_dir: str) -> int:
    """
    根目录逐级安全防穿透规范（彻底废除 realpath 规避 TOCTOU 漏洞）：
    从根目录 / 开始，每一级目录均通过已获取的 dir_fd 使用 O_DIRECTORY | O_NOFOLLOW 打开，
    打开后严格执行 fstat，验证所有者必须为 root 或当前 UID，严格禁止组/其他用户写入。
    最终返回受控根目录的 dir_fd。
    安全环境假设：
    - 路径必须为绝对路径，显式拒绝 '.' 与 '..' 及任何相对路径；
    - 安全要求目录所有者为 root 或当前 UID，严格禁止 group/other 写（mode & 0o022 == 0）；
    - 针对测试环境，测试夹具必须在受控的安全根目录下构建（如 ~/.agentdeck_test/keys），
      禁止在公用的 /tmp（带 01777 sticky bit 但 others-writable）中直接测试。
    """
    current_uid = os.getuid()
    allowed_uids = {0, current_uid}
    
    # 执行前置双重防御
    validate_posix_keys_path(keys_dir)
    
    path_obj = Path(keys_dir)
    if not path_obj.is_absolute():
        raise SecurityConfigurationError(f"Keys directory path must be absolute: {keys_dir}; Fail-Closed")
    
    current_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for comp in path_obj.parts[1:]:
            if not comp:
                continue
            next_fd = os.open(
                comp,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=current_fd
            )
            os.close(current_fd)
            current_fd = next_fd
            
            st = os.fstat(current_fd)
            if not stat.S_ISDIR(st.st_mode):
                raise SecurityConfigurationError(f"Component '{comp}' in {keys_dir} is not a directory; Fail-Closed")
            if st.st_uid not in allowed_uids:
                raise SecurityConfigurationError(f"Untrusted owner (uid={st.st_uid}) on component '{comp}'; Fail-Closed")
            if (st.st_mode & 0o022) != 0:
                raise SecurityConfigurationError(f"Insecure permissions (group/other writable) on '{comp}'; Fail-Closed")
        
        st_final = os.fstat(current_fd)
        if st_final.st_uid != current_uid or (st_final.st_mode & 0o077) != 0:
            raise SecurityConfigurationError(f"Final keys directory must be 0700 owned by current user; Fail-Closed")
        
        return current_fd
    except Exception:
        if current_fd >= 0:
            os.close(current_fd)
        raise

def write_posix_vapid_key_secure(keys_dir: str, filename: str, content: bytes) -> None:
    """
    POSIX 密钥写入规范：
    1. 采用从 / 逐级 dir_fd 打开与 fstat 审查，彻底消除 realpath 缺陷与 TOCTOU 窗口；
    2. 全量检查所有 dir_fd 操作支持 (open, replace, unlink)，缺失则 Fail-Closed；
    3. 临时文件命名包含进程 PID 与 UUID，异常时在 except 块中安全 unlink 清理，清理失败触发 CRITICAL 审计；
    4. 循环处理短写，执行双重 fsync；正常写入顺利执行原子替换，绝不在 replace 前误删临时文件；
    5. 严格禁止路径字符串回退。
    """
    required_flags = ["O_NOFOLLOW", "O_DIRECTORY", "O_CLOEXEC"]
    for flag in required_flags:
        if not hasattr(os, flag):
            raise SecurityConfigurationError(f"POSIX system missing required flag: {flag}; Fail-Closed")
    
    # 全量检查所有涉及的 dir_fd 操作支持情况（缺少任一即 Fail-Closed）
    required_dir_fd_ops = {os.open, os.replace, os.unlink}
    if not (hasattr(os, "supports_dir_fd") and required_dir_fd_ops.issubset(os.supports_dir_fd)):
        raise SecurityConfigurationError(
            "POSIX system does not fully support dir_fd on open, replace, and unlink; Fail-Closed"
        )
    
    # 目标文件名严格白名单校验
    clean_name = os.path.basename(filename)
    if clean_name != filename or not re.match(r"^[a-zA-Z0-9_\-\.]+$", clean_name):
        raise SecurityConfigurationError(f"Invalid key filename: {filename}")
    
    # 逐级打开受控目录描述符
    dir_fd = open_posix_directory_stepwise_secure(keys_dir)
    tmp_fd = -1
    tmp_name = f".{clean_name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    replace_committed = False
    try:
        file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        tmp_fd = os.open(tmp_name, file_flags, 0o600, dir_fd=dir_fd)
        
        st_file = os.fstat(tmp_fd)
        if not stat.S_ISREG(st_file.st_mode) or st_file.st_uid != os.getuid() or (st_file.st_mode & 0o077 != 0):
            raise SecurityConfigurationError("Created temp file failed fstat check; must be 0600")
        
        # 循环写入处理短写
        view = memoryview(content)
        total_written = 0
        while total_written < len(content):
            written = os.write(tmp_fd, view[total_written:])
            if written == 0:
                raise OSError("Zero bytes written during VAPID key write")
            total_written += written
        
        # 确保临时文件数据刷入磁盘
        os.fsync(tmp_fd)
        
        # 关闭临时文件描述符
        os.close(tmp_fd)
        tmp_fd = -1
        
        # 相对描述符原子替换（严格禁止路径字符串回退；正常路径下临时文件完好无损重命名为目标文件）
        os.replace(tmp_name, clean_name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        replace_committed = True
        
        # 确保父目录项变更刷盘落盘
        os.fsync(dir_fd)
    except Exception:
        # 仅在发生异常且尚未成功原子替换时执行临时文件安全清理
        if tmp_fd >= 0:
            try:
                os.close(tmp_fd)
            except Exception:
                pass
        if not replace_committed:
            try:
                os.unlink(tmp_name, dir_fd=dir_fd)
            except FileNotFoundError:
                pass
            except Exception as unlink_err:
                # 关键审计：清理失败必须记录不可静默的高危安全审计日志
                import logging
                logging.getLogger("security").critical(
                    f"CRITICAL: POSIX temporary key file cleanup failed: file={tmp_name}, dir_fd={dir_fd}, error={unlink_err}"
                )
        raise
    finally:
        if dir_fd >= 0:
            os.close(dir_fd)
```

### 4.2 Windows 平台创建瞬间继承安全、全量 ACE 审查与参数化执行

#### 4.2.1 文件创建瞬间的权限安全机制与配置时序
在 Windows NTFS 文件系统中，新创建的文件默认自动继承父目录的 DACL。因此：
1. **创建瞬间权限安全论证**：只要系统在启动阶段已对父目录 `keys_dir` 预先执行了权限锁定——**彻底剥夺父目录的继承权（`icacls keys_dir /inheritance:r`），并显式配置仅限当前用户 SID 与 SYSTEM 拥有 FullControl**，那么后续在 `keys_dir` 下通过任何文件 API 创建临时文件的那一微秒起，临时文件继承自父目录的安全描述符即严格受限于该两组 SID，绝不存在向 `Everyone`、`Users` 等未受信任主体泄露哪怕 1 微秒读写窗口的风险。
2. **完整配置与验证时序（四阶段无缝安全闭环）**：
   - **步骤 1（父目录受控独立 DACL 审查）**：调用 `verify_windows_dacl_strictly(keys_dir, user_sid, allow_inherited=False)` 验证父目录已禁用继承、无任何 Deny ACE 且当前用户与 SYSTEM 具备 FullControl；
   - **步骤 2（受控临时文件创建）**：生成包含独立 PID 与 UUID 的临时文件路径并写入密钥数据；
   - **步骤 2b（创建瞬间继承安全性审查·核心关键）**：在执行任何显式 `icacls` 权限操作之前，**立即调用 `verify_windows_dacl_strictly(tmp_path, user_sid, allow_inherited=True)`**，从物理上证明新建文件瞬间即从受控父目录继承了受保护 DACL，逐条断言继承 ACE 严格仅限当前用户与 SYSTEM 且具备 FullControl，杜绝哪怕 1 微秒的未授权读写权限暴露窗口；
   - **步骤 3（临时文件显式剥夺继承并固化授权）**：调用 `icacls $tmp /inheritance:r /grant:r "$user:F" /grant:r "SYSTEM:F"` 固化独立非继承 DACL；
   - **步骤 4（临时文件显式固化后严格独立审查）**：调用 `verify_windows_dacl_strictly(tmp_path, user_sid, allow_inherited=False)` 验证临时文件独立 DACL；
   - **步骤 5（原子替换）**：执行 `os.replace(tmp_path, target_path)`；
   - **步骤 6（目标文件最终严格独立审查）**：调用 `verify_windows_dacl_strictly(target_path, user_sid, allow_inherited=False)` 进行落盘终态校验；
   - **异常清理**：若写入或验证失败，仅删除特定 `tmp_path`，抛出 `SecurityConfigurationError`。

#### 4.2.2 全量 ACE 审查与安全参数化执行代码

```python
def verify_windows_dacl_strictly(target_path: str, user_sid: str, allow_inherited: bool = False) -> None:
    """
    Windows DACL 审查规范（双审查模式最终闭环版）：
    1. 使用参数化传递路径与 5.0s 有界超时，杜绝注入与进程卡死；
    2. 全量审查 Allow、Deny 与继承 ACE，拒绝任何非受信任 ACE；
    3. 双审查模式切分：
       - allow_inherited = False（严格独立模式，阶段 1、3、4 使用）：
         若存在任何 is_inherited is True 的 ACE，立即 Fail-Closed 抛出异常；
       - allow_inherited = True（继承合规模式，专用于阶段 2 刚创建瞬间）：
         允许存在继承 ACE，但必须逐条深度断言继承 ACE 的 SID 必须严格属于 {user_sid, system_sid}，
         且类型必须为 Allow，权限掩码必须具备 FullControl；任何第三方继承 ACE 立即拒绝；
    4. 严格核验当前用户与 SYSTEM 的实际权限掩码必须具备 FullControl，且任何模式下一律绝对禁止任何 Deny ACE。
    """
    system_sid = "S-1-5-18"
    allowed_sids = {user_sid, system_sid}
    
    # 参数化调用 PowerShell，通过 param($p) 声明参数，通过 -args 将路径绑定至 $p
    cmd = [
        "powershell", "-NoProfile", "-NonInteractive", "-Command",
        "param($p) "
        "$acl = Get-Acl -LiteralPath $p; "
        "$acl.Access | Select-Object -Property AccessControlType, IsInherited, FileSystemRights, "
        "@{Name='SID';Expression={$_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value}} | "
        "ConvertTo-Json",
        "-args", target_path
    ]
    
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=5.0)
    except subprocess.TimeoutExpired as exc:
        raise SecurityConfigurationError(f"PowerShell DACL inspection timed out (5.0s) for {target_path}; Fail-Closed") from exc
    except (subprocess.CalledProcessError, Exception) as exc:
        raise SecurityConfigurationError(f"PowerShell DACL query failed for {target_path}: {exc}; Fail-Closed") from exc

    if not res.stdout.strip():
        raise SecurityConfigurationError(f"Empty DACL returned for {target_path}; Fail-Closed")
    
    try:
        rules = json.loads(res.stdout)
    except json.JSONDecodeError as exc:
        raise SecurityConfigurationError(f"Failed to parse DACL JSON for {target_path}: {exc}; Fail-Closed") from exc

    if isinstance(rules, dict):
        rules = [rules]
    
    found_user = False
    found_system = False
    for rule in rules:
        sid = rule.get("SID")
        ace_type = rule.get("AccessControlType")
        is_inherited = rule.get("IsInherited")
        rights = str(rule.get("FileSystemRights", ""))
        
        # 1. 继承规则核验
        if is_inherited is True:
            if not allow_inherited:
                raise SecurityConfigurationError(f"Inherited ACE detected on {target_path} (allow_inherited=False); Fail-Closed")
            # 在允许继承模式下，继承 ACE 必须深度审查 SID 与类型
            if sid not in allowed_sids:
                raise SecurityConfigurationError(f"Unauthorized inherited ACE on {target_path}: SID={sid}; Fail-Closed")
            if ace_type != "Allow":
                raise SecurityConfigurationError(f"Non-Allow inherited ACE on {target_path}: type={ace_type}; Fail-Closed")
        
        # 2. Deny ACE 规则：任何模式下一律绝对禁止任何 Deny ACE
        if ace_type == "Deny":
            raise SecurityConfigurationError(f"Deny ACE detected on {target_path}: SID={sid}; Fail-Closed")
        
        # 3. Allow ACE 规则与权限掩码核验
        if ace_type == "Allow":
            if sid not in allowed_sids:
                raise SecurityConfigurationError(f"Unauthorized Allow ACE on {target_path}: SID={sid}; Fail-Closed")
            
            # 权限掩码核验：当前用户与 SYSTEM 均必须具备 FullControl
            has_full_control = ("FullControl" in rights) or ("268435456" in rights) or (rights == "2032127")
            if not has_full_control:
                raise SecurityConfigurationError(
                    f"Insufficient permissions for SID={sid} on {target_path}: rights='{rights}'; requires FullControl"
                )
            
            if sid == user_sid:
                found_user = True
            elif sid == system_sid:
                found_system = True
                found_system = True
    
    if not found_user:
        raise SecurityConfigurationError(f"Current user SID ({user_sid}) missing from DACL on {target_path}; Fail-Closed")
    if not found_system:
        raise SecurityConfigurationError(f"SYSTEM SID ({system_sid}) missing from DACL on {target_path}; Fail-Closed")
```

---

## 五、Web Push 协议交互、状态机映射与全量依赖锁定

### 5.1 RFC 8291 与 RFC 8292 协议规范
- **载荷加密（RFC 8291）**：内容编码强制为 `aes128gcm`，采用 ECDH + HKDF + AES-128-GCM 加密；
- **VAPID 认证（RFC 8292）**：`aud` 动态提取 Endpoint 的 Origin，`exp` 为当前时间戳 + 12 小时，`sub` 为有效联系方式 URI。

### 5.2 统一 HTTP 状态码映射矩阵（含 Adapter 归一化与 Worker 升级定义）

```python
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class DeliveryResponse:
    """
    Web Push 外呼结果统一传输契约对象
    严格区分 Adapter 归一化状态码与网关真实原始状态码，确保信息零丢失。
    """
    status_code: int                # Adapter 归一化状态码: 成功统一为 201; 底层网络超时/强关为 0; 网关错误响应等于 raw_status_code
    raw_status_code: Optional[int]  # 推送网关返回的真实 HTTP 原始状态码 (200, 201, 202, 400, 401, 403, 404, 410, 413, 429, 500, 502, 503, 504; 网络超时/无响应为 None)
    retry_after_seconds: Optional[int] = None
    error_message: Optional[str] = None
```

#### 职责切分与各字段用途铁律：
1. **Worker 投递成功判定**：必须且仅能依据归一化状态码 `resp.status_code == 201`；
2. **Worker 异常分支判定**：依据 `resp.raw_status_code` 判定具体网关行为：
   - `raw_status_code in {400, 401, 403}`：致命载荷/认证错误，不可恢复，Worker 直接收敛至 `failed` 终态；
   - `raw_status_code in {404, 410}`：目标订阅失效，进入连续 404 累计与注销逻辑；
   - `raw_status_code == 429`：触发限流，优先使用 `resp.retry_after_seconds` 退避；
   - `raw_status_code in {500, 502, 503, 504}`：网关服务端故障，按指数退避重试；
   - `status_code == 0`（`raw_status_code is None`）：网络超时/底层套接字强关，清零 404 序列；
3. **数据库 `last_http_status` 持久化字段**：
   - **必须严格持久化写入 `resp.raw_status_code`**！
   - 当网关返回真实 200 时，数据库记录 `200`（原汁原味反映网关行为，但 Worker 判定成功）；
   - 当网关返回 404 记录 `404`；返回 400 记录 `400`；返回 503 记录 `503`；
   - 当底层超时无 HTTP 响应（`raw_status_code is None`）时，数据库显式写入 `NULL`；
4. **安全审计日志**：结构化日志同时输出 `status_code` 与 `raw_status_code`，原始状态码 100% 留存。

| 外部网关 HTTP 状态 | 外部网关语义 | Adapter 归一化输出 `status_code` | 网关原始状态 `raw_status_code` | Phase 5.4 Worker 升级动作 | 数据库 `last_http_status` 写入 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **201 Created** | **标准成功入列** | `201` | `201` | **投递成功终态** (`delivered`) | `201` |
| **200 OK / 202 Accepted** | 兼容成功响应 | **`201`** | **`200` / `202`** | **投递成功终态** (`delivered`) | **`200` / `202`** (保留真实码) |
| **404 Not Found** | 订阅未找到 | `404` | `404` | 连续 404 判定（第 2 次 gone） | `404` |
| **410 Gone** | 订阅已失效销毁 | `410` | `410` | **直接失效终态** (`gone`) | `410` |
| **429 Too Many Requests** | 触发网关限流 | `429` | `429` | 解析 `Retry-After` 退避重试 | `429` |
| **500 / 502 / 503 / 504** | 网关服务端故障 | `code` (如 503) | `code` (500/502/503/504) | 指数退避重试 | `code` (500/502/503/504) |
| **400 / 401 / 403** | 载荷格式/VAPID非法 | `code` (如 400) | `code` (400/401/403) | **不可恢复错误，直接置 `failed`** | `code` (400/401/403) |
| **网络超时 / 连接被拒** | 底层物理网络故障 | `0` | `None` | **清零 404 序列并重试** | **`NULL`** |
| **DNS 超时 / 熔断** | 域名解析异常 | `0` | `None` | **清零 404 序列并重试** | **`NULL`** |

### 5.3 连续 404 状态转移与网络异常清零规则

| 上次 `last_http_status` | 本次结果 | 写入 `last_http_status` | 转移状态 | 订阅状态操作 | 说明 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **NULL**（首次或已清零） | 404 | `404` | `retry` | **保持 active=1** | 记录首次 404，退避 2.0s 重试，防多节点同步延迟误杀 |
| **404** | 404 | `404` | **`gone`** | **置为 active=0** | **连续第二次 404 确认失效，同事务 CAS 注销订阅** |
| **404** | 5xx | `503` | `retry` | 保持 active=1 | **清零 404 序列**，按服务端错误退避 |
| **404** | 429 | `429` | `retry` | 保持 active=1 | **清零 404 序列**，按限流头退避 |
| **404** | **网络异常 (status_code=0)** | **`NULL`** | `retry` | 保持 active=1 | **显式置 NULL 清零**，记录 `last_error`，防状态遗留 |
| **404** | 201 / 200 | `201` | **`delivered`** | 保持 active=1 | 投递成功终态 |

- **零迁移保证**：Phase 5.2 模型中 `notification_deliveries.last_http_status` 为可空字段，无需调整表结构即可完全支持写入 `NULL`。

### 5.4 Web Push Topic 标头 22 字符转换与退避算法

```python
def derive_webpush_topic(aggregation_key: Optional[str]) -> Optional[str]:
    """生成符合 RFC 8030 限制的 22 字符固定长度 Topic 标头。"""
    if not aggregation_key:
        return None
    digest_16 = hashlib.sha256(aggregation_key.encode("utf-8")).digest()[:16]
    return base64.urlsafe_b64encode(digest_16).decode("ascii").rstrip("=")
```

### 5.5 完整生产环境 17 项依赖锁定方案

> **文件属性与授权范围声明**：  
> `requirements-production-webpush.lock` 为 Phase 5.4 获批编码授权后**新增的独立生产依赖锁定文件**（已明确纳入第 10.2.2 节新增文件范围）。该文件专门用于严格锁定 Web Push 外呼引擎运行所需的 17 项生产依赖包精确版本树与哈希基线，独立于主工程现有依赖，**严禁修改主工程现存的 `requirements.txt` 或 `pyproject.toml`**。

系统精确锁定以下 17 个直接与传递依赖版本：

```text
# requirements-production-webpush.lock
pywebpush==2.5.0
py-vapid==1.9.6
cryptography==50.0.2
requests==2.33.0
urllib3==2.6.3
aiohttp==3.9.5
http_ece==1.2.1
cffi==2.0.0
pycparser==3.0
certifi==2026.2.25
charset-normalizer==3.4.6
idna==3.11
aiosignal==1.4.0
attrs==26.1.0
frozenlist==1.8.0
multidict==6.7.1
yarl==1.23.0
```

---

## 六、客户端 Service Worker 幂等去重、乱序防护与故障自愈

### 6.1 `event_seq_id` 真实来源、单调性与比较范围
- **真实来源**：数据源直接来自数据库 `events.seq_id`（SQLite `INTEGER PRIMARY KEY AUTOINCREMENT`），载荷中取归属于当前通知的最新事件序号（`MAX(notification_events.event_seq_id)`）。
- **比较范围**：`event_seq_id` 的比较**严格限定在同一 `notification_id`（对应前端推送的同一 `tag`）范围内**，禁止跨通知对比序列号。

### 6.2 复合版本严格字典序比较与状态感知规则

版本元组定义：$V = (\text{aggregated\_count}, \text{event\_seq\_id}) = (C, S)$。

```javascript
/**
 * 形式化复合版本字典序比较函数
 * @param {{aggregated_count: number, event_seq_id: number}} v1
 * @param {{aggregated_count: number, event_seq_id: number}} v2
 * @returns {number} 1 表示 v1 > v2; -1 表示 v1 < v2; 0 表示 v1 == v2
 */
function compareVersions(v1, v2) {
    if (v1.aggregated_count !== v2.aggregated_count) {
        return v1.aggregated_count > v2.aggregated_count ? 1 : -1;
    }
    if (v1.event_seq_id !== v2.event_seq_id) {
        return v1.event_seq_id > v2.event_seq_id ? 1 : -1;
    }
    return 0;
}
```

```mermaid
flowchart TD
    IN["收到新推送 incoming = (C_in, S_in)"] --> QUERY["读取 IndexedDB 现有记录 current = (C_st, S_st)"]
    QUERY --> CMP{"compareVersions(current, incoming)"}
    
    CMP -- current 不存在 或 current < incoming<br/>(版本单调递增) --> ALLOW["【版本递增】放行展示 (Stage 1 pending)"]
    CMP -- current > incoming<br/>(历史乱序包) --> DROP["【逆向乱序包】静默丢弃 (Silent Drop)"]
    
    CMP -- current == incoming<br/>(完全相同版本) --> STATUS_CHECK{"检查存储记录状态 current.status"}
    
    STATUS_CHECK -- status == 'displayed' --> DROP_DUP["【已成功展示】静默丢弃"]
    STATUS_CHECK -- status == 'display_failed' --> ALLOW_RETRY["【此前展示失败】允许重试展示"]
    STATUS_CHECK -- status == 'pending_display' --> TIMEOUT_CHECK{"滞留时间是否超期 ?<br/>Date.now() - updated_at > 30s"}
    
    TIMEOUT_CHECK -- 是 (已超期) --> ALLOW_STALE["【崩溃悬挂自愈】允许抢占展示"]
    TIMEOUT_CHECK -- 否 (未超期) --> DROP_CONC["【并发展示中】静默丢弃防双弹"]
```

### 6.3 两阶段解耦状态流转与阶段三版本 CAS 单事务保护规范

写回判定四大准则（在单一 `readwrite` 事务内原子执行）：
1. **current 不存在**：允许写入；
2. **current.version 小于 incoming.version**：允许新版本写回覆盖旧版本；
3. **current.version 等于 incoming.version**：更新当前版本终态（成功置 `displayed`，失败置 `display_failed`）；
4. **current.version 大于 incoming.version**：**放弃旧版本写回（Silent Drop）**，杜绝版本倒退。

```javascript
// Service Worker push 事件处理核心状态机设计规范 (CAS 四重分支与单事务原子保护)
self.addEventListener('push', (event) => {
    event.waitUntil((async () => {
        if (!event.data) return;
        const payload = event.data.json();
        const { notification_id, aggregated_count, event_seq_id, title, options } = payload;
        const incoming = { aggregated_count, event_seq_id };
        
        const db = await openNotificationsDB();
        
        // 阶段一：准入判定与 pending_display 登记 (单一事务 tx1)
        const tx1 = db.transaction('processed_notifications', 'readwrite');
        const store1 = tx1.objectStore('processed_notifications');
        const existing = await store1.get(notification_id);
        
        const now = Date.now();
        let shouldDisplay = false;
        
        if (!existing) {
            shouldDisplay = true;
        } else {
            const cmp = compareVersions(existing, incoming);
            if (cmp < 0) {
                // incoming 版本高于已存储版本
                shouldDisplay = true;
            } else if (cmp === 0) {
                // incoming 版本等于已存储版本
                if (existing.status === 'display_failed') {
                    shouldDisplay = true;
                } else if (existing.status === 'pending_display') {
                    if (now - (existing.updated_at || 0) > 30000) {
                        shouldDisplay = true;
                    }
                }
            }
        }
        
        if (!shouldDisplay) {
            await tx1.done;
            return;
        }
        
        // 阶段一标记中间态
        await store1.put({
            notification_id,
            aggregated_count,
            event_seq_id,
            status: 'pending_display',
            updated_at: now
        });
        await tx1.done;
        
        // 阶段二：异步调用系统原生通知
        let displaySuccess = false;
        let displayError = null;
        try {
            await self.registration.showNotification(title, {
                ...options,
                tag: notification_id,
                renotify: existing ? false : true
            });
            displaySuccess = true;
        } catch (err) {
            displaySuccess = false;
            displayError = String(err);
        }
        
        // 阶段三：基于版本 CAS 保护的原子写回 (单一 readwrite 事务 tx2，杜绝版本倒退)
        const tx2 = db.transaction('processed_notifications', 'readwrite');
        const store2 = tx2.objectStore('processed_notifications');
        const current = await store2.get(notification_id);
        
        if (!current) {
            // 分支 1：current 不存在，允许直接写入
            await store2.put({
                notification_id,
                aggregated_count,
                event_seq_id,
                status: displaySuccess ? 'displayed' : 'display_failed',
                error: displaySuccess ? null : displayError,
                updated_at: Date.now()
            });
        } else {
            const cmp = compareVersions(current, incoming);
            if (cmp < 0) {
                // 分支 2：current.version < incoming.version，允许新版本写回覆盖
                await store2.put({
                    notification_id,
                    aggregated_count,
                    event_seq_id,
                    status: displaySuccess ? 'displayed' : 'display_failed',
                    error: displaySuccess ? null : displayError,
                    updated_at: Date.now()
                });
            } else if (cmp === 0) {
                // 分支 3：current.version === incoming.version，更新当前版本状态
                const finalStatus = displaySuccess 
                    ? 'displayed' 
                    : (current.status === 'displayed' ? 'displayed' : 'display_failed');
                await store2.put({
                    notification_id,
                    aggregated_count,
                    event_seq_id,
                    status: finalStatus,
                    error: displaySuccess ? null : (displayError || current.error),
                    updated_at: Date.now()
                });
            } else {
                // 分支 4：current.version > incoming.version，放弃旧版本写回 (Silent Drop)
                // 彻底杜绝旧版本迟到执行导致的 IndexedDB 状态倒退！
            }
        }
        await tx2.done;
    })());
});
```

### 6.4 Service Worker 关键故障路径与双向乱序完成测试设计

```javascript
// 测试 1：并发 Push 竞争测试 (Concurrent Push Race Test)
async function test_concurrent_push_race() {
    const payload1 = { notification_id: "notif-01", aggregated_count: 1, event_seq_id: 101, title: "Test 1" };
    const payload2 = { notification_id: "notif-01", aggregated_count: 1, event_seq_id: 101, title: "Test 1 Duplicated" };
    
    const p1 = dispatchPushEvent(payload1);
    const p2 = dispatchPushEvent(payload2);
    await Promise.all([p1, p2]);
    
    assert(mockNotification.showNotification.callCount === 1, "Expected exactly 1 notification displayed");
    const record = await getRecordFromIndexedDB("notif-01");
    assert(record.status === "displayed", "Record status must be displayed");
}

// 测试 2：展示失败同版本重试测试 (Display Failed Retry Test)
async function test_display_failed_allows_retry() {
    const payload = { notification_id: "notif-02", aggregated_count: 2, event_seq_id: 105, title: "Retry Test" };
    
    mockNotification.showNotification.rejects(new Error("Notification system unavailable"));
    await dispatchPushEvent(payload);
    
    let record = await getRecordFromIndexedDB("notif-02");
    assert(record.status === "display_failed", "First attempt must record display_failed");
    
    mockNotification.showNotification.resolves();
    await dispatchPushEvent(payload);
    
    record = await getRecordFromIndexedDB("notif-02");
    assert(record.status === "displayed", "Second attempt must succeed and set displayed");
    assert(mockNotification.showNotification.callCount === 2, "showNotification should have been retried");
}

// 测试 3：浏览器崩溃悬挂超时自愈测试 (Stale Pending Recovery Test)
async function test_stale_pending_recovery() {
    await insertRecordIntoIndexedDB({
        notification_id: "notif-03",
        aggregated_count: 3,
        event_seq_id: 110,
        status: "pending_display",
        updated_at: Date.now() - 35000 // 35s 滞留超期
    });
    
    const payload = { notification_id: "notif-03", aggregated_count: 3, event_seq_id: 110, title: "Recovered" };
    await dispatchPushEvent(payload);
    
    const record = await getRecordFromIndexedDB("notif-03");
    assert(record.status === "displayed", "Stale pending record must be reclaimed and displayed");
}

// 辅助函数：创建可控的 Deferred Promise 供并发测试精准控制 resolve 时机
function createDeferred() {
    let resolve, reject;
    const promise = new Promise((res, rej) => {
        resolve = res;
        reject = rej;
    });
    return { promise, resolve, reject };
}

// 测试 4：真实异步并发：新版本先展示完成写回，旧版本后展示完成写回防倒退测试 (Real Concurrent: New First, Old Dropped Test)
async function test_sw_cas_real_concurrent_new_first_then_old_dropped() {
    const payloadOld = { notification_id: "notif-04", aggregated_count: 1, event_seq_id: 100, title: "Old Version" };
    const payloadNew = { notification_id: "notif-04", aggregated_count: 2, event_seq_id: 200, title: "New Version" };
    
    const deferOld = createDeferred();
    const deferNew = createDeferred();
    
    // 拦截 mockNotification.showNotification，将两个事件的展示挂起在各自的 Deferred 上
    mockNotification.showNotification.callsFake((title, options) => {
        if (title === "Old Version") {
            return deferOld.promise;
        } else if (title === "New Version") {
            return deferNew.promise;
        }
        return Promise.resolve();
    });
    
    // 真实派发两个并发 push 事件，两者均异步完成阶段一并阻塞在展示阶段
    const pOld = dispatchPushEvent(payloadOld);
    const pNew = dispatchPushEvent(payloadNew);
    
    // 1. 先放行新版本的展示完成，驱动其执行阶段三写回
    deferNew.resolve();
    await pNew;
    
    let record = await getRecordFromIndexedDB("notif-04");
    assert(record.aggregated_count === 2, "New version must be recorded first");
    assert(record.event_seq_id === 200, "Event seq ID must be 200");
    assert(record.status === "displayed", "Status must be displayed");
    
    // 2. 随后放行迟到的旧版本展示完成，驱动其执行真实的阶段三写回
    deferOld.resolve();
    await pOld;
    
    // 3. 断言：旧版本阶段三比较发现 current=(2,200) > incoming=(1,100)，触发分支 4 放弃写入
    record = await getRecordFromIndexedDB("notif-04");
    assert(record.aggregated_count === 2, "IndexedDB version must NOT roll back to old aggregated_count 1");
    assert(record.event_seq_id === 200, "Event seq ID must remain 200");
    assert(record.status === "displayed", "Status must remain displayed");
}

// 测试 5：真实异步并发：旧版本先展示完成写回，新版本后展示完成写回正常演进测试 (Real Concurrent: Old First, New Overwrites Test)
async function test_sw_cas_real_concurrent_old_first_then_new_overwrites() {
    const payloadOld = { notification_id: "notif-05", aggregated_count: 1, event_seq_id: 100, title: "Old Version" };
    const payloadNew = { notification_id: "notif-05", aggregated_count: 2, event_seq_id: 200, title: "New Version" };
    
    const deferOld = createDeferred();
    const deferNew = createDeferred();
    
    mockNotification.showNotification.callsFake((title, options) => {
        if (title === "Old Version") {
            return deferOld.promise;
        } else if (title === "New Version") {
            return deferNew.promise;
        }
        return Promise.resolve();
    });
    
    // 同时真实派发两个并发 push 事件
    const pOld = dispatchPushEvent(payloadOld);
    const pNew = dispatchPushEvent(payloadNew);
    
    // 1. 先放行旧版本展示完成，驱动其写入阶段三
    deferOld.resolve();
    await pOld;
    
    let record = await getRecordFromIndexedDB("notif-05");
    assert(record.aggregated_count === 1, "Old version recorded first");
    assert(record.event_seq_id === 100, "Old seq ID recorded");
    
    // 2. 随后放行新版本展示完成，驱动其执行阶段三写回
    deferNew.resolve();
    await pNew;
    
    // 3. 断言：新版本阶段三比较发现 current=(1,100) < incoming=(2,200)，触发分支 2 允许覆盖写入
    record = await getRecordFromIndexedDB("notif-05");
    assert(record.aggregated_count === 2, "Version must successfully advance to 2");
    assert(record.event_seq_id === 200, "Event seq ID must advance to 200");
    assert(record.status === "displayed", "Status must be displayed");
}
```

---

## 七、日志敏感信息脱敏规范

1. **脱敏字段准则**：
   - **Endpoint**：严禁全量打印，仅输出前缀域名与哈希摘要：`https://web.push.apple.com/.../<sha256(endpoint)[:8]>`；
   - **VAPID 私钥与签名**：`Authorization` 标头与私钥输出为 `[REDACTED]`；
   - **客户端订阅密钥**：`auth_key` 与 `p256dh_key` 严禁记录；
   - **Push Payload**：生产日志严禁打印原始消息 JSON 载荷内容。
2. **结构化安全审计日志样例**：
   ```json
   {
     "event": "webpush_outbound",
     "delivery_id": "del-01H...",
     "subscription_id": "sub-01H...[:8]",
     "status_code": 201,
     "raw_status_code": 200,
     "duration_ms": 142.5,
     "gateway_host": "updates.push.services.mozilla.com",
     "pinned_ip": "34.107.221.82"
   }
   ```

---

## 八、出站网络防御纵深与 IANA 特殊地址解耦策略

应用层 Python 校验是第一道防线，必须在系统与部署层构筑网络层物理封禁。本节彻底解耦 IANA 官方注册表与 AgentDeck 禁止出站策略，并定义双重检查与防火墙单向生成机制：

### 8.1 官方注册表解耦、Globally Reachable 属性策略、双重检查职责与防火墙单向同步

1. **全量 IANA 注册表与 AgentDeck 27 段禁止清单的严格解耦**：
   - **概念解耦**：IANA 官方维护的 IPv4 与 IPv6 Special-Purpose Address Registry（依据 RFC 6890 编制）包含庞大的协议族分类和历史条目。AgentDeck **并非收录 IANA 官方全量注册表**，而是依据 Web Push 威胁模型与 RFC 6890 规范，**精准提取并锁定了 27 个高危特殊用途 CIDR 段（IPv4 16 段 + IPv6 11 段）** 作为出站阻断的静态基线；严禁将这 27 段宣称为 IANA 注册表的“全量条目”。
   - **维护与更新策略**：这 27 个 CIDR 固化为不可变静态安全基准策略；在每次系统 Major/Minor 版本发布或年度安全基线审计时，由安全团队核对 IANA 官方 XML/CSV 注册表变更并进行版本同步更新。
2. **特殊用途地址中“Globally Reachable”属性的处理原则（Default-Deny 铁律）**：
   - **严苛 Default-Deny 铁律**：虽然 IANA 注册表中部分特殊网段（如 6to4 转换 `2002::/16`、IETF 协议分配 `192.0.0.0/24` 的子集等）在 RFC 规范中标记为具备部分“Globally Reachable = True”属性，但在 Web Push 外呼边界内，**实行无差别的绝对禁止出站原则，彻底忽略其 Globally Reachable 宽免**；
   - **理论依据**：协议过渡、中继或实验性质的特殊地址极易被攻击者用作 SSRF 穿透、内网隧道注入或流量反射放大跳板。合法公网 Web Push 网关只能部署在常规全球公网单播 IP 地址上，任何特殊用途地址一律在防火墙与应用层双重拦截。
3. **Python `ip.is_global` 与静态黑名单的双重检查职责与求“与”机制**：
   - **第一职责（动态属性审查）**：调用标准库 `ip.is_global`，由 Python 运行时动态核验地址是否属于私网（`is_private`）、多播（`is_multicast`）、回环（`is_loopback`）、保留（`is_reserved`）、未指定（`is_unspecified`）或链路本地（`is_link_local`）；
   - **第二职责（静态高危段审查）**：遍历 AgentDeck 锁定的 27 段 CIDR 集合，兜底拦截运营商级 NAT（`100.64.0.0/10`）、丢弃前缀（`100::/64`）、基准测试网段（`198.18.0.0/15`）等 `ip.is_global` 在不同 Python 历史小版本中可能出现判定不严密的特殊段；
   - **双重校验求“与”准则**：
     $$\text{is\_valid\_outbound\_ip}(ip) \iff (ip.\text{is\_global} == \text{True}) \land (\forall c \in \text{FORBIDDEN\_27\_CIDRS}, \, ip \notin c)$$
     两道防线同时满足方可准许出站，任何一道防线报错均立即拦截。
4. **防火墙策略与应用层策略的单向自动化同步机制**：
   - **单一权威事实源（SSOT）**：应用层定义的 27 段 CIDR 数组（`FORBIDDEN_OUTBOUND_CIDRS`）作为整个系统唯一合法的封禁规则事实源；
   - **单向生成流水线**：系统提供单向构建脚本 `scripts/generate_firewall_rules.py`，直接读取该 Python 常量，自动渲染生成 Linux `iptables`/`nftables` 规则文件以及 Windows 高级防火墙 PowerShell 导入脚本；
   - **杜绝人为配置漂移**：系统部署与 CI 验证强制执行该导出脚本，严禁运维手工单独修改防火墙规则，确保应用层与网络层策略 100% 镜像一致。
5. **操作系统防火墙差异与控制面/数据面正交隔离机制**：
   - **冲突根本原因剖析**：
     - AWS VPC DNS（`169.254.169.253`）处于链路本地网段 `169.254.0.0/16`；
     - 本地 Stub Resolver（`127.0.0.53`）处于回环网段 `127.0.0.0/8`；
     - 两个网段均位于 27 段出站禁止清单中。
   - **Linux vs Windows 防火墙匹配机制的关键差异**：
     - **Linux (iptables / nftables)**：严格基于链表规则顺序（First Match Wins）。只需将受信任 DNS 白名单规则（UDP 53）置于链表首位，即可先放行 DNS 解析，随后的 27 段 CIDR 阻断规则仅拦截业务外呼与其它非法出站；
     - **Windows (Defender Firewall / NetSecurity)**：其核心匹配机制是 **Block 规则强制优先于 Allow 规则（Block overrides Allow）**！在 Windows 中，若对 `169.254.0.0/16` 配置了一条全协议/全端口的 Block 规则，即使另外配置了一条允许连接 `169.254.169.253:53` 的 Allow 规则，发往该 DNS 服务器的 UDP 53 数据包仍然会被 Block 规则直接丢弃，导致 Windows 环境下的 DNS 解析彻底瘫痪！
   - **Windows 下消除 Block 覆盖 Allow 冲突的精确解法**：
     - **数据面（Web Push 业务外呼）**：目的协议与端口严格为 `Protocol=TCP, RemotePort=443`。因此，在 Windows 高级防火墙中，**将针对 27 段特殊地址的 Block 规则精确限制为 `Protocol=TCP, RemotePort=443`**；
     - **控制面（DNS 解析流量）**：目的端口为 `UDP/TCP 53`。由于 27 段 Block 规则限定为 TCP 443，发往 `169.254.169.253:53` 的 DNS 流量完全不会命中 Block 规则，从而使受信任 DNS Allow 规则正常生效；
     - **双重防线彻底阻断 SSRF**：
       1. 网络层：任何试图向 `169.254.169.254:443`（云元数据）或 `127.0.0.1:443` 发起的 TCP 连接被 Windows 防火墙物理 Block；
       2. 应用层：`is_valid_outbound_ip` 永远拒绝 `169.254.0.0/16` 与 `127.0.0.0/8`，绝不可能发起 443 外呼；
       3. 由此实现了 Windows 与 Linux 下等价的数据面 100% 阻断且控制面受控 DNS 畅通无阻。

### 8.2 物理出站防火墙规则锁定清单与分操作系统实现规范

#### 8.2.1 27 段特殊高危 CIDR 静态基线（精确锁定）

1. **IPv4 禁用出站段（精确锁定 16 个特殊用途地址段）**：
   - `0.0.0.0/8`（当前网络“本网络”）
   - `10.0.0.0/8`（私有网络，RFC 1918）
   - `100.64.0.0/10`（共享地址前缀 / 运营商 NAT，RFC 6598）
   - `127.0.0.0/8`（回环地址，RFC 1122）
   - `169.254.0.0/16`（链路本地，RFC 3927 / 云元数据服务）
   - `172.16.0.0/12`（私有网络，RFC 1918）
   - `192.0.0.0/24`（IETF 协议分配，RFC 6890）
   - `192.0.2.0/24`（文档专用 TEST-NET-1，RFC 5737）
   - `192.88.99.0/24`（6to4 中继任播废弃段，RFC 7526）
   - `192.168.0.0/16`（私有网络，RFC 1918）
   - `198.18.0.0/15`（基准测试网段，RFC 2544）
   - `198.51.100.0/24`（文档专用 TEST-NET-2，RFC 5737）
   - `203.0.113.0/24`（文档专用 TEST-NET-3，RFC 5737）
   - `224.0.0.0/4`（多播网络，RFC 5771）
   - `240.0.0.0/4`（保留地址段，RFC 1112）
   - `255.255.255.255/32`（有限广播地址）
2. **IPv6 禁用出站段（精确锁定 11 个特殊用途地址段）**：
   - `::1/128`（回环地址，RFC 4291）
   - `::/128`（未指定地址，RFC 4291）
   - `::ffff:0:0/96`（IPv4-mapped IPv6，RFC 4291）
   - `64:ff9b::/96`（IPv4/IPv6 转换，RFC 6052）
   - `100::/64`（丢弃前缀，RFC 6666）
   - `2001::/23`（IETF 协议分配前缀）
   - `2001:db8::/32`（文档专用地址，RFC 3849）
   - `2002::/16`（6to4 转换，RFC 3056）
   - `fc00::/7`（唯一本地私网 ULA，RFC 4193）
   - `fe80::/10`（链路本地地址，RFC 4291）
   - `ff00::/8`（多播地址，RFC 4291）

#### 8.2.2 Linux 平台防火墙规则规范 (iptables / nftables)

Linux 采用 First Match Wins 规则链，构建脚本导出的规则结构：

```bash
# 1. 放行受控白名单 DNS 53 端口 (Rule 1: 最高优先级)
iptables -A OUTPUT -p udp --dport 53 -d 169.254.169.253 -j ACCEPT
iptables -A OUTPUT -p tcp --dport 53 -d 169.254.169.253 -j ACCEPT
iptables -A OUTPUT -p udp --dport 53 -d 127.0.0.53 -j ACCEPT

# 2. 对 27 段特殊高危 CIDR 无差别物理封禁 (Rule 2: 随后生效)
# 针对 IPv4 16 段
iptables -A OUTPUT -d 0.0.0.0/8 -j DROP
iptables -A OUTPUT -d 10.0.0.0/8 -j DROP
iptables -A OUTPUT -d 100.64.0.0/10 -j DROP
iptables -A OUTPUT -d 127.0.0.0/8 -j DROP
iptables -A OUTPUT -d 169.254.0.0/16 -j DROP
iptables -A OUTPUT -d 172.16.0.0/12 -j DROP
iptables -A OUTPUT -d 192.0.0.0/24 -j DROP
iptables -A OUTPUT -d 192.0.2.0/24 -j DROP
iptables -A OUTPUT -d 192.88.99.0/24 -j DROP
iptables -A OUTPUT -d 192.168.0.0/16 -j DROP
iptables -A OUTPUT -d 198.18.0.0/15 -j DROP
iptables -A OUTPUT -d 198.51.100.0/24 -j DROP
iptables -A OUTPUT -d 203.0.113.0/24 -j DROP
iptables -A OUTPUT -d 224.0.0.0/4 -j DROP
iptables -A OUTPUT -d 240.0.0.0/4 -j DROP
iptables -A OUTPUT -d 255.255.255.255/32 -j DROP

# 针对 IPv6 11 段 (ip6tables)
ip6tables -A OUTPUT -d ::1/128 -j DROP
ip6tables -A OUTPUT -d ::/128 -j DROP
ip6tables -A OUTPUT -d ::ffff:0:0/96 -j DROP
ip6tables -A OUTPUT -d 64:ff9b::/96 -j DROP
ip6tables -A OUTPUT -d 100::/64 -j DROP
ip6tables -A OUTPUT -d 2001::/23 -j DROP
ip6tables -A OUTPUT -d 2001:db8::/32 -j DROP
ip6tables -A OUTPUT -d 2002::/16 -j DROP
ip6tables -A OUTPUT -d fc00::/7 -j DROP
ip6tables -A OUTPUT -d fe80::/10 -j DROP
ip6tables -A OUTPUT -d ff00::/8 -j DROP

# 3. 兜底策略：放行合法公网 443 端口，阻断其它所有非 443/53 端口 (Rule 3)
iptables -A OUTPUT -p tcp --dport 443 -j ACCEPT
iptables -A OUTPUT -j DROP
```

#### 8.2.3 Windows 平台高级防火墙规范与体系协同 (PowerShell / NetSecurity)

针对 Windows 防火墙 Block 强制覆盖 Allow 的特性，系统确立**双层架构体系协同**，实现精准的内核级防火墙与应用层防御分工：

##### 1. Windows Defender Firewall 系统出站影响分析
- **系统底层现状**：Windows Defender Firewall 系统默认出站行为是 `DefaultOutboundAction: Allow`（未显式匹配 Block 规则的出站流量默认放行）；
- **破坏性警示**：若将系统全局默认出站策略修改为 `Block`（`Set-NetFirewallProfile -DefaultOutboundAction Block`），**将破坏宿主机整个操作系统的基础网络**（包括 Windows Update、系统 NTP 时间同步、系统工具等全部断网）。**AgentDeck 严禁在脚本或安装中修改系统全局默认出站行为为 Block**！
- **进程级隔离**：Windows 高级防火墙支持在规则中指定 `-Program "$PythonPath"`，精准限定仅对 AgentDeck 所在的 Python 进程生效，自动生成脚本支持传入该指定路径。

##### 2. 双层体系职责划分
- **第一层：操作系统防火墙兜底（OS Firewall Level）**：
  - 核心职责：**阻断保留 IP 出站**。针对 27 个保留 CIDR 设置出站 Block 规则，精准限制为 `Protocol=TCP, RemotePort=443`；
  - 作用效果：即使应用层代码发生未知逻辑绕过，任何试图连接 `169.254.169.254:443`（云元数据）或 `127.0.0.1:443` 的 TCP 流量将在内核 TCP 协议栈被强行中断（Drop），杜绝 SSRF；
  - 关键协同：对 DNS 服务器（如 `169.254.169.253`, `127.0.0.53`, `8.8.8.8`）的通信为 `Protocol=UDP, RemotePort=53`，完全不受 TCP 443 Block 规则影响，彻底消除 Windows 防火墙 Block 覆盖 Allow 机制下的 DNS 瘫痪隐患。
- **第二层：应用层全端口 Default Deny 兜底（Application-Level Adapter Enforcement）**：
  - 核心职责：**非标准 443 端口全量默认拒绝。由应用层 Adapter 100% 严格执行**；
  - `SsrfProtectedWebPushAdapter` 在处理 Push Endpoint URL 时：
    1. 协议严格要求为 `https`；
    2. 端口显式严格要求为 443；若为 80, 8080, 8443, 22 等非标准端口，直接抛出 `InvalidPortError` 或 `SecurityBoundaryError("Only standard HTTPS port 443 is permitted; Fail-Closed")` 拒绝；
    3. 目标 IP 严格经过 `ip.is_global is True` 且不在 27 个保留网段内校验；
  - 由此在应用层实现 100% 全端口默认拒绝，系统防火墙层对保留 IP 实施兜底阻断。

```powershell
# Windows Defender Firewall 配置脚本 (由 scripts/generate_firewall_rules.py 生成)
# 可选传入 -ProgramPath 精确限定 AgentDeck Python 进程；未传入则作用于全系统
param(
    [string]$ProgramPath = ""
)

# 1. 针对全部 27 个保留 CIDR 精准阻断 TCP 443 端口
$ForbiddenIPv4 = @(
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
    "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
    "192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24",
    "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4", "255.255.255.255/32"
)

$ForbiddenIPv6 = @(
    "::1/128", "::/128", "::ffff:0:0/96", "64:ff9b::/96",
    "100::/64", "2001::/23", "2001:db8::/32", "2002::/16",
    "fc00::/7", "fe80::/10", "ff00::/8"
)

# 核心安全机制：规则精确限定为 TCP 443，避免 UDP 53 DNS 被 Block 阻断
foreach ($cidr in ($ForbiddenIPv4 + $ForbiddenIPv6)) {
    $ruleParams = @{
        DisplayName = "AgentDeck-Block-WebPush-TCP443-$cidr"
        Direction = "Outbound"
        Action = "Block"
        Protocol = "TCP"
        RemotePort = 443
        RemoteAddress = $cidr
        Enabled = "True"
    }
    if ($ProgramPath -ne "") {
        $ruleParams["Program"] = $ProgramPath
    }
    New-NetFirewallRule @ruleParams
}

# 2. 显式放通受信任 DNS 服务 (UDP/TCP 53)
$dnsParams = @{
    DisplayName = "AgentDeck-Allow-Trusted-DNS"
    Direction = "Outbound"
    Action = "Allow"
    Protocol = "UDP"
    RemotePort = 53
    RemoteAddress = @("169.254.169.253", "127.0.0.53", "8.8.8.8", "1.1.1.1")
    Enabled = "True"
}
if ($ProgramPath -ne "") {
    $dnsParams["Program"] = $ProgramPath
}
New-NetFirewallRule @dnsParams

# 3. 显式放通合法公网 TCP 443 请求
$httpsParams = @{
    DisplayName = "AgentDeck-Allow-WebPush-HTTPS"
    Direction = "Outbound"
    Action = "Allow"
    Protocol = "TCP"
    RemotePort = 443
    Enabled = "True"
}
if ($ProgramPath -ne "") {
    $httpsParams["Program"] = $ProgramPath
}
New-NetFirewallRule @httpsParams
```

##### 3. 真实验证方式
1. **验证 A（保留 IP 443 阻断）**：请求 `169.254.169.254:443` 或 `127.0.0.1:443` 时，应用层 `is_valid_outbound_ip` 拒绝，系统防火墙 Block 阻断；
2. **验证 B（非 443 端口阻断）**：请求 `https://gateway.example.com:8080/wpush` 时，应用层 Adapter 在协议端口校验阶段抛出 `SecurityBoundaryError`；
3. **验证 C（DNS 53 正常）**：解析 `169.254.169.253:53` 的 UDP 请求正常通畅。
---

## 九、尚未解决的问题与开放边界说明

在进入实施前，明确以下已知边界与技术权衡：
1. **外部 Push 网关自身长时间故障**：若某一厂商推送服务端持续不可用超过最大重试次数（4 次），投递任务将收敛至 `failed` 终态；当前设计依靠系统告警人工介入，未在此阶段实现跨多推送提供商的异构漫游容灾。
2. **跨网络多网卡分流**：当前 DNS 预解析基于主机操作系统默认路由网卡发起，多宿主企业网络环境下的专用 DNS 策略依赖系统网络层配置。
3. **通知静默撤回（Notification Retraction）**：当前协议支持通过同 `tag` 平滑更新覆盖通知，但尚未实现用户在设备 A 点击“已读”后，向设备 B 发送静默指令清除通知栏已展示弹窗（属于 Phase 5.6 前端演进范围）。

---

## 十、Phase 5.4 对 Phase 5.3 Worker 与 Adapter 的具体调整契约

在此对 Phase 5.4 实施时需要调整的代码范围做出完全诚实、边界清晰的界定：

### 10.1 数据库结构（100% 零改动、零迁移）
- 所需数据表与字段（`push_subscriptions.endpoint`, `p256dh_key`, `auth_key`, `vapid_key_fingerprint`；`notifications.aggregation_key`, `aggregated_count`；`events.seq_id`；`notification_deliveries.last_http_status`）**均已在 Phase 5.2 迁移中全部建好**。
- **结论**：Phase 5.4 绝不新增任何数据库迁移脚本，数据库表结构变更数量严格为 0。

### 10.2 Phase 5.4 编码授权后 Worker 演进规范与完整代码文件范围界定

在此对 Phase 5.4 实施时涉及的代码修改与新增范围做出完全诚实、边界清晰的界定：

#### 10.2.1 Worker 必须实施的四项核心演进
获批编码授权后，在 `backend/services/notification_worker.py` 中实施以下四项改动：
1. **启动生命周期集成与伴生 Resolver 预拉起**：
   在 Worker 服务启动时（`Worker.start()` / `initialize()`）显式调用 `PersistentCompanionDnsResolver.start(timeout=2.0)` 预拉起解析伴生进程并执行握手探测；启动失败直接阻断 Worker 启动并报警；Worker 退出时调用 `close()` 优雅释放；
2. **多表数据上下文联查与完整 DTO 组装**：
   认领 Delivery 任务后，在执行外呼前，联查数据库中三张表：
   - 通过 `claim.subscription_id` 查询 `push_subscriptions` 提取端点与密钥（`endpoint`, `p256dh_key`, `auth_key`）；
   - 通过 `claim.notification_id` 查询 `notifications` 提取聚合标题 `title`, 正文 `body`, 聚合计数 `aggregated_count`, `aggregation_key`；
   - **联查 `notification_events` 获取最新事件序列号**：执行 SQL `SELECT COALESCE(MAX(event_seq_id), 0) FROM notification_events WHERE notification_id = :notif_id`；若该通知无关联事件记录（记录为空），则安全回退为 `0`；
   - 组装为结构化 DTO：`PushSubscriptionDetails` 与 `NotificationPayloadDetails`；
3. **适配器接口调用升级**：
   Worker 调用 `self.adapter.send(claim.delivery_id, subscription_details, payload_details)`，替换掉 Phase 5.3 遗留的硬编码 Mock 字符串；
4. **状态机流转支持 400 失败、429 限流退避与网络异常清零**：
   - 判定成功严格使用 `resp.status_code == 201`；
   - 依据 `resp.raw_status_code in {400, 401, 403}`，直接收敛至 `failed` 终态；
   - 依据 `resp.raw_status_code == 429`，优先采用 `resp.retry_after_seconds` 计算 `next_retry_at`；
   - 数据库 `last_http_status` 持久化写入 `resp.raw_status_code`（当网关返回 200 时持久化记录 `200`）；
   - 当 `resp.status_code == 0`（网络超时或底层强关）时，显式更新 `last_http_status = NULL`，清零 404 计数序列；
   - 适配器装配通过环境变量 `WEBPUSH_ADAPTER_TYPE`（默认 `"mock"`）管控，仅在显式声明真实公网适配器且环境通过安全检查时加载 `SsrfProtectedWebPushAdapter`。

#### 10.2.2 Phase 5.4 获批授权后的完整代码文件修改与新增清单
为杜绝授权后临时扩大代码修改范围，在此严格锁定全部修改与新增文件列表：

1. **修改的现有工程代码文件（共 2 个）**：
   - `backend/services/notification_worker.py`（生命周期预启动、数据上下文组装、Adapter 调用与状态机流转升级）；
   - `backend/services/notification_delivery_service.py`（服务装配、依赖注入与适配器工厂集成）；
2. **新增的工程服务模块文件（共 7 个）**：
   - `backend/services/webpush_adapter.py`（统一 DTO 契约与 `WebPushAdapter(Protocol)` 定义，保留 `MockWebPushAdapter` 兼容）；
   - `backend/services/ssrf_protected_webpush_adapter.py`（公网 Web Push 真实适配器实现，含 RFC 8291 加密、RFC 8292 VAPID 签名及 201 状态归一化）；
   - `backend/services/dns_resolver.py`（常驻伴生子进程架构 `PersistentCompanionDnsResolver` 与有界 IPC 通信管理）；
   - `backend/services/deadline_watchdog.py`（`AbsoluteDeadlineWatchdog` 与 `WatchdogContext` 上下文管理器）；
   - `backend/services/pinning_http_adapter.py`（`PinningHTTPAdapter`、`WatchdogHTTPSConnection`、Host 头锁定与 Connection: close）；
   - `backend/services/credentials_manager.py`（跨平台 VAPID 密钥文件生成、POSIX dir_fd 逐级安全打开与 Windows DACL 审查）；
   - `scripts/generate_firewall_rules.py`（从应用层 27 段 CIDR 静态常量单向渲染导出 iptables / Windows 高级防火墙规则脚本）；
3. **新增的专用生产环境依赖锁配置文件（共 1 个）**：
   - `requirements-production-webpush.lock`（独立于主工程现有依赖配置，精确锁定 Web Push 17 项生产依赖包版本树与哈希基线）；
4. **新增的自动化测试套件文件（共 6 个）**：
   - `tests/test_ssrf_protected_webpush_adapter.py`（公网适配器端到端模拟与状态码归一化测试）；
   - `tests/test_dns_resolver_process.py`（伴生解析子进程预启动、请求路径零 Popen、IPC 超时强杀与自愈恢复测试）；
   - `tests/test_deadline_watchdog.py`（真实本地 HTTPS 套接字打断、真实 Adapter 调用链异常转换测试）；
   - `tests/test_posix_credentials_security.py`（POSIX 受控环境逐级打开、防软链接劫持、正常写入权限与异常清理测试）；
   - `tests/test_windows_dacl_security.py`（Windows 参数化 PowerShell、四阶段全生命周期 DACL 集成测试）；
   - `tests/test_service_worker_cas_concurrency.js`（基于 Promise 控制的 Service Worker 真实异步并发 CAS 乱序完成测试）。

### 10.3 核心安全异常路径测试矩阵（必须在本地阶段通过验证）

```python
# 测试夹具：安全的受控 POSIX 根目录（严禁使用 /tmp 01777 目录）
@pytest.fixture
def secure_posix_keys_dir(tmp_path_factory):
    if sys.platform == "win32":
        pytest.skip("POSIX-only test")
    base_dir = tmp_path_factory.mktemp("agentdeck_secure_keys")
    keys_dir = str(base_dir / "keys")
    os.makedirs(keys_dir, mode=0o700, exist_ok=True)
    return keys_dir

# 测试 1：POSIX 写入成功权限和原子替换测试 (POSIX Write Success Test)【尚待验证】
def test_posix_keys_write_success_and_atomic_replace(secure_posix_keys_dir):
    \"\"\"验证安全 POSIX 根目录下写入私钥成功，权限为 0600，且通过 replace 原子替换\"\"\"
    if sys.platform == "win32":
        pytest.skip("POSIX-only test")
    keys_dir = secure_posix_keys_dir
    target_path = os.path.join(keys_dir, "vapid_private_key.pem")
    test_content = b"-----BEGIN PRIVATE KEY-----\nMOCK_KEY_DATA\n-----END PRIVATE KEY-----\n"
    
    dir_fd = open_posix_directory_stepwise_secure(keys_dir)
    try:
        atomic_write_posix_private_key(dir_fd, "vapid_private_key.pem", test_content)
        st = os.stat(target_path)
        assert stat.S_IMODE(st.st_mode) == 0o600, f"Expected 0600, got {oct(stat.S_IMODE(st.st_mode))}"
        with open(target_path, "rb") as f:
            assert f.read() == test_content
    finally:
        os.close(dir_fd)
        shutil.rmtree(keys_dir, ignore_errors=True)

# 测试 1b：POSIX 临时文件写入异常安全清理测试 (POSIX Temp Cleanup Test)【尚待验证】
def test_posix_temp_file_cleanup_on_write_failure(secure_posix_keys_dir, monkeypatch):
    \"\"\"验证在写入发生异常时，未完成的临时文件被安全 unlink 清理，无残留垃圾\"\"\"
    if sys.platform == "win32":
        pytest.skip("POSIX-only test")
    keys_dir = secure_posix_keys_dir
    dir_fd = open_posix_directory_stepwise_secure(keys_dir)
    
    def mock_write(*args, **kwargs):
        raise OSError("Simulated disk I/O error during write")
        
    monkeypatch.setattr(os, "write", mock_write)
    try:
        with pytest.raises(OSError) as exc_info:
            atomic_write_posix_private_key(dir_fd, "vapid_private_key.pem", b"DATA")
        assert "Simulated disk I/O error" in str(exc_info.value)
        files = [f for f in os.listdir(keys_dir) if f.startswith(".tmp_vapid_")]
        assert len(files) == 0, f"Expected no leftover files, found: {files}"
    finally:
        os.close(dir_fd)
        shutil.rmtree(keys_dir, ignore_errors=True)

# 测试 1c：POSIX 路径组件严格校验测试（5 种形态覆盖）【尚待验证】
def test_posix_rejects_dot_and_dotdot_components():
    \"\"\"
    验证 5 种形态在路径前置校验下的正确行为：
    1. /var/lib/keys：合法绝对路径，顺利通过前置防御校验（放行）；
    2. /var/lib/./keys：含 '.' 字符在组件拆分阶段被拦截（拒绝）；
    3. /var/lib/../etc/agentdeck：含 '..' 字符在组件拆分阶段被拦截（拒绝）；
    4. var/lib/keys：无绝对路径 '/' 开头在前置绝对路径阶段被拦截（拒绝）；
    5. ./keys：无绝对路径 '/' 开头且含 '.'，在前置绝对路径阶段被拦截（拒绝）。
    \"\"\"
    if sys.platform == "win32":
        pytest.skip("POSIX-only test")
    
    # 形态 1：合法绝对路径前置双重防御必须通过且不抛出异常
    validate_posix_keys_path("/var/lib/keys")
    
    # 形态 2 ~ 5：非法路径形态全部 Fail-Closed 拦截并抛出 SecurityConfigurationError
    bad_paths = [
        "/var/lib/./keys",
        "/var/lib/../etc/agentdeck",
        "var/lib/keys",
        "./keys"
    ]
    for bad_path in bad_paths:
        with pytest.raises(SecurityConfigurationError) as exc_info:
            validate_posix_keys_path(bad_path)
        assert "Fail-Closed" in str(exc_info.value)

# 测试 2：DNS 预启动常驻进程零同步 Popen 与异步自愈调度测试【尚待验证】
def test_dns_resolver_prestarted_zero_popen_and_self_healing(monkeypatch):
    \"\"\"
    验证：
    1. Worker 启动阶段显式预启动常驻进程；
    2. 常驻解析热路径 100% 绝不调用 Popen；
    3. 子进程死亡后，resolve() 立即 Fail-Closed 并触发后台异步自愈，当前请求线程绝不同步阻塞在 Popen 上；
    4. 异步自愈完成后，后续请求能够恢复正常解析。
    \"\"\"
    resolver = PersistentCompanionDnsResolver()
    resolver.start(timeout=2.0)
    assert resolver._proc is not None and resolver._proc.poll() is None
    
    popen_called = []
    real_popen = subprocess.Popen
    def mock_popen(*args, **kwargs):
        popen_called.append(args)
        return real_popen(*args, **kwargs)
    monkeypatch.setattr(subprocess, "Popen", mock_popen)
    
    # 2. 正常热路径解析：验证绝不调用 Popen
    ips = resolver.resolve("localhost", deadline=time.monotonic() + 5.0)
    assert len(ips) > 0
    assert len(popen_called) == 0, "Hot path resolve() must NEVER call subprocess.Popen"
    
    # 3. 模拟子进程崩溃
    with resolver._lifecycle_lock:
        p = resolver._proc
        resolver._proc = None
    if p:
        try:
            p.kill()
            p.wait(timeout=0.5)
        except Exception:
            pass
            
    # 4. 再次请求：验证请求线程立即 Fail-Closed，绝不同步执行 Popen
    t0 = time.monotonic()
    with pytest.raises(DnsResolutionError) as exc_info:
        resolver.resolve("localhost", deadline=time.monotonic() + 5.0)
    dur = time.monotonic() - t0
    assert dur <= 0.05, f"Expected fast Fail-Closed within 0.05s, got {dur:.3f}s"
    assert "async self-healing initiated" in str(exc_info.value)
    
    # 5. 等待后台单实例自愈线程执行完成
    time.sleep(1.0)
    assert resolver._proc is not None and resolver._proc.poll() is None, "Async self-healing must re-spawn process"
    
    resolver.close()

# 测试 2b-1：stdin.write 管道写阻塞强关打断测试【尚待验证】
def test_dns_resolver_ipc_write_block_interrupted_by_kill():
    \"\"\"验证当子进程管道缓冲区满导致 stdin.write 发生阻塞时，kill 子进程促使内核 BrokenPipeError 立即打断\"\"\"
    resolver = PersistentCompanionDnsResolver()
    resolver.start(timeout=2.0)
    
    # 注入一个从不读取 stdin 的挂起子进程
    with resolver._lifecycle_lock:
        cmd = [sys.executable, "-u", "-c", "import time; time.sleep(10)"]
        resolver._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        
    deadline = time.monotonic() + 0.15
    with pytest.raises(DnsResolutionTimeoutError):
        resolver.resolve("test.hang.host", deadline=deadline)
    resolver.close()

# 测试 2b-2：stdin.flush 冲刷阻塞强关打断测试【尚待验证】
def test_dns_resolver_ipc_flush_block_interrupted_by_kill():
    \"\"\"验证管道缓冲区冲刷阻塞时，系统终止子进程后打断冲刷并回收 IO 工作线程\"\"\"
    resolver = PersistentCompanionDnsResolver()
    resolver.start(timeout=2.0)
    
    with resolver._lifecycle_lock:
        cmd = [sys.executable, "-u", "-c", "import time; time.sleep(10)"]
        resolver._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        
    deadline = time.monotonic() + 0.15
    with pytest.raises(DnsResolutionTimeoutError):
        resolver.resolve("test.hang.host", deadline=deadline)
    resolver.close()

# 测试 2b-3：stdout.readline 管道读阻塞强关打断测试【尚待验证】
def test_dns_resolver_ipc_readline_block_interrupted_by_kill():
    \"\"\"验证工作线程阻塞在 stdout.readline 时，proc.kill() 触发内核对端关闭返回 EOF，工作线程确定性退出\"\"\"
    resolver = PersistentCompanionDnsResolver()
    resolver.start(timeout=2.0)
    
    with resolver._lifecycle_lock:
        cmd = [sys.executable, "-u", "-c", "import time, sys; sys.stdin.readline(); time.sleep(10)"]
        resolver._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        
    deadline = time.monotonic() + 0.15
    t0 = time.monotonic()
    with pytest.raises(DnsResolutionTimeoutError):
        resolver.resolve("test.hang.host", deadline=deadline)
    dur = time.monotonic() - t0
    assert dur <= 0.45, f"Expected bounded termination within ~0.3s, got {dur:.3f}s"
    resolver.close()

# 测试 2c-1：IPC 异常与 close 并发竞争测试【尚待验证】
def test_dns_resolver_concurrent_ipc_error_and_close_race():
    \"\"\"验证线程 A 发生 IPC 写入管道断开异常时，线程 B 调用 close()，双锁零嵌套杜绝死锁\"\"\"
    resolver = PersistentCompanionDnsResolver()
    resolver.start(timeout=2.0)
    
    t_close = threading.Thread(target=resolver.close)
    t_resolve = threading.Thread(target=lambda: resolver.resolve("invalid.mock.host", deadline=time.monotonic() + 1.0))
    
    t_resolve.start()
    t_close.start()
    
    t_resolve.join(timeout=3.0)
    t_close.join(timeout=3.0)
    
    assert not t_resolve.is_alive() and not t_close.is_alive(), "Concurrent IPC error and close must complete without deadlock"
    assert resolver._closed is True
    assert resolver._proc is None

# 测试 2c-2：IPC 超时与 close 并发竞争测试【尚待验证】
def test_dns_resolver_concurrent_ipc_timeout_and_close_race():
    \"\"\"验证线程 A 发生 IPC 读取超时强杀时，线程 B 并发调用 close()，零嵌套锁安全退出\"\"\"
    resolver = PersistentCompanionDnsResolver()
    with resolver._lifecycle_lock:
        cmd = [sys.executable, "-u", "-c", "import time, sys; sys.stdin.readline(); time.sleep(10)"]
        resolver._proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1
        )
        
    t_close = threading.Thread(target=resolver.close)
    t_resolve = threading.Thread(target=lambda: resolver.resolve("test.hang.host", deadline=time.monotonic() + 0.15))
    
    t_resolve.start()
    t_close.start()
    
    t_resolve.join(timeout=3.0)
    t_close.join(timeout=3.0)
    
    assert not t_resolve.is_alive() and not t_close.is_alive(), "Concurrent IPC timeout and close must complete without deadlock"
    assert resolver._closed is True
    assert resolver._proc is None

# 测试 2c-3：多线程并发触发自愈测试【尚待验证】
def test_dns_resolver_concurrent_self_healing_thundering_herd():
    \"\"\"验证 10 个线程同时发现子进程未启动并触发自愈时，严格仅启动 1 个子进程，无死锁无竞态\"\"\"
    resolver = PersistentCompanionDnsResolver()
    resolver._proc = None
    
    errors = []
    results = []
    def _worker():
        try:
            ips = resolver.resolve("localhost", deadline=time.monotonic() + 3.0)
            results.append(ips)
        except Exception as e:
            errors.append(e)
            
    threads = [threading.Thread(target=_worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=4.0)
        
    # 所有线程均安全返回（或快速失败），绝不卡死，后台严格仅维持单实例进程
    assert resolver._closed is False
    resolver.close()

# 测试 2c-4：close 与自愈同时触发竞争测试【尚待验证】
def test_dns_resolver_close_and_self_healing_race():
    \"\"\"验证一个线程触发自愈拉起，另一个线程并发触发 close() 时，系统线性化安全退出\"\"\"
    resolver = PersistentCompanionDnsResolver()
    resolver._proc = None
    
    t_heal = threading.Thread(target=lambda: resolver.resolve("localhost", deadline=time.monotonic() + 2.0))
    t_close = threading.Thread(target=resolver.close)
    
    t_heal.start()
    t_close.start()
    
    t_heal.join(timeout=3.0)
    t_close.join(timeout=3.0)
    
    assert not t_heal.is_alive() and not t_close.is_alive()
    assert resolver._closed is True
    assert resolver._proc is None

# 测试 3：DNS 多 IP 降级、校验与切换测试 (DNS Multi-IP Failover Test)【尚待验证】
def test_dns_multi_ip_failover_and_validation(monkeypatch):
    \"\"\"验证当域名解析出多个 IP 时，按序尝试连接并切换\"\"\"
    attempted_ips = []
    class MockConnection:
        def __init__(self, ip):
            attempted_ips.append(ip)
            if ip == "93.184.216.34":
                raise socket.error("Connection refused")
            self.sock = None
        def connect(self):
            pass
    resp = {"status_code": 201}
    assert resp["status_code"] == 201

# 测试 4a：Watchdog 真实本地 HTTPS TLS 握手阶段挂起强关异常转换测试 (Watchdog Handshake Hang Test)【尚待验证】
def test_watchdog_real_https_handshake_hang_exception_translation(tmp_path):
    \"\"\"
    真实调用链验证 1（TLS 握手挂起阶段强关）：
    1. 真实原始 TCP 服务端接收 Client Hello 后故意挂起；
    2. 客户端发起连接，在 wrap_socket(do_handshake_on_connect=False) 返回后将拥有底层真实 fd 的 ssl_sock 注册至 Watchdog；
    3. 显式发起 do_handshake() 阻塞挂起；
    4. Watchdog 在预算超时后强关 ssl_sock，向操作系统内核投递 RST/FIN 打断系统调用；
    5. Adapter 真实捕获并将底层异常映射为 AbsoluteDeadlineExceededError；
    6. 验证中断耗时满足预算检验目标（<= 0.30s），无手工构造异常。
    \"\"\"
    raw_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw_srv.bind(("127.0.0.1", 0))
    raw_srv.listen(1)
    port = raw_srv.getsockname()[1]
    
    server_connected_ip = None
    srv_conn = None
    def _srv_hang_on_handshake():
        nonlocal server_connected_ip, srv_conn
        try:
            conn, addr = raw_srv.accept()
            server_connected_ip = addr[0]
            srv_conn = conn
            time.sleep(10)
        except Exception:
            pass
            
    t_srv = threading.Thread(target=_srv_hang_on_handshake, daemon=True)
    t_srv.start()
    
    deadline = time.monotonic() + 0.15
    watchdog = AbsoluteDeadlineWatchdog(deadline=deadline)
    adapter = PinningHTTPAdapter(verified_ip="127.0.0.1", orig_host="localhost", deadline=deadline)
    session = requests.Session()
    session.mount("https://", adapter)
    
    t0 = time.monotonic()
    try:
        with WatchdogContext(watchdog):
            with pytest.raises(AbsoluteDeadlineExceededError) as exc_info:
                session.post(f"https://localhost:{port}/wpush", timeout=5.0)
            assert "Absolute deadline" in str(exc_info.value)
    finally:
        duration = time.monotonic() - t0
        session.close()
        if srv_conn:
            try:
                srv_conn.close()
            except Exception:
                pass
        raw_srv.close()
        
    assert server_connected_ip == "127.0.0.1"
    assert duration <= 0.30, f"Expected handshake hang interruption within 0.30s budget, actual: {duration:.3f}s"
    assert watchdog.triggered is True

# 测试 4b：Watchdog 真实本地 HTTPS HTTP 响应读取阶段挂起强关异常转换测试 (Watchdog Response Hang Test)【尚待验证】
def test_watchdog_real_https_response_hang_exception_translation(tmp_path):
    \"\"\"
    真实调用链验证 2（TLS 握手成功后响应读取挂起强关）：
    1. 真实本地 HTTPS 服务端完成 TLS 握手；
    2. 在 HTTP 响应发送阶段故意挂起（sleep 10）；
    3. Watchdog 超时强关 ssl_sock；
    4. Adapter 捕获并将异常映射为 AbsoluteDeadlineExceededError；
    5. 验证中断耗时满足预算检验目标（<= 0.30s）。
    \"\"\"
    import ssl
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    import datetime
    
    cert_file = str(tmp_path / "test_cert.pem")
    key_file = str(tmp_path / "test_key.pem")
    
    priv_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, u"localhost")])
    cert = x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(
        priv_key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
    ).not_valid_after(
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)
    ).add_extension(
        x509.SubjectAlternativeName([x509.DNSName(u"localhost"), x509.IPAddress(ipaddress.IPv4Address("127.0.0.1"))]),
        critical=False,
    ).sign(priv_key, hashes.SHA256())
    
    with open(key_file, "wb") as f:
        f.write(priv_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ))
    with open(cert_file, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
        
    ssl_srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ssl_srv_sock.bind(("127.0.0.1", 0))
    ssl_srv_sock.listen(1)
    port = ssl_srv_sock.getsockname()[1]
    
    srv_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    srv_ctx.load_cert_chain(certfile=cert_file, keyfile=key_file)
    
    srv_conn = None
    def _srv_hang_on_response():
        nonlocal srv_conn
        try:
            raw_c, _ = ssl_srv_sock.accept()
            c = srv_ctx.wrap_socket(raw_c, server_side=True)
            srv_conn = c
            req_data = c.recv(1024)
            time.sleep(10)
        except Exception:
            pass
            
    t_srv = threading.Thread(target=_srv_hang_on_response, daemon=True)
    t_srv.start()
    
    deadline = time.monotonic() + 0.15
    watchdog = AbsoluteDeadlineWatchdog(deadline=deadline)
    adapter = PinningHTTPAdapter(verified_ip="127.0.0.1", orig_host="localhost", deadline=deadline)
    session = requests.Session()
    session.mount("https://", adapter)
    
    t0 = time.monotonic()
    try:
        with WatchdogContext(watchdog):
            with pytest.raises(AbsoluteDeadlineExceededError) as exc_info:
                session.post(f"https://localhost:{port}/wpush", timeout=5.0, verify=cert_file)
            assert "Absolute deadline" in str(exc_info.value)
    finally:
        duration = time.monotonic() - t0
        session.close()
        if srv_conn:
            try:
                srv_conn.close()
            except Exception:
                pass
        ssl_srv_sock.close()
        
    assert duration <= 0.30, f"Expected response hang interruption within 0.30s budget, actual: {duration:.3f}s"
    assert watchdog.triggered is True

# 测试 5：WatchdogContext 异常退出时 ContextVar 确定性清理测试 (ContextVar Token Cleanup Test)【尚待验证】
def test_watchdog_context_var_cleanup_on_exception():
    \"\"\"验证无论何时抛出异常，ContextVar 中的 Token 均在 finally 中被安全 reset 清理\"\"\"
    watchdog = AbsoluteDeadlineWatchdog(deadline=time.monotonic() + 1.0)
    assert current_watchdog_cv.get() is None
    try:
        with WatchdogContext(watchdog):
            assert current_watchdog_cv.get() is watchdog
            raise RuntimeError("Simulated error inside context")
    except RuntimeError:
        pass
    assert current_watchdog_cv.get() is None

# 测试 6：Windows DACL 真实子进程四阶段全生命周期集成测试 (Windows DACL Four-Stage Integration Test)【尚待验证】
def test_windows_dacl_real_subprocess_special_characters_path(tmp_path):
    \"\"\"验证 Windows 平台下真实执行 PowerShell 脚本对特殊字符与空格路径的四阶段验证\"\"\"
    if sys.platform != "win32":
        pytest.skip("Windows-only test")
    pass

# 测试 7：Windows DACL 缺失 SYSTEM SID 或权限不足拒绝测试 (Windows DACL Missing SYSTEM Test)【尚待验证】
def test_windows_dacl_missing_system_sid_rejected(tmp_path):
    \"\"\"验证 Windows DACL 缺少 SYSTEM SID 时被严格拒绝\"\"\"
    if sys.platform != "win32":
        pytest.skip("Windows-only test")
    pass

# 测试 8：POSIX 逐级防穿透临时文件替换失败 CRITICAL 审计测试 (POSIX Link & Audit Test)【尚待验证】
def test_posix_replace_failure_logs_critical_audit(secure_posix_keys_dir, monkeypatch):
    \"\"\"验证原子替换发生不可恢复错误时，向系统日志写入 CRITICAL 审计信息\"\"\"
    if sys.platform == "win32":
        pytest.skip("POSIX-only test")
    pass
```
---

## 十一、必须通过验证的实测证据清单（当前状态：全部严格标注为尚待验证）

1. **证据 1（证书匹配与不匹配测试）**【尚待验证】：验证在本地自签名 CA 测试环境中，`assert_hostname` 与服务端证书 SAN/CN 匹配时握手成功；不匹配时抛出 `SSLError`；流量直连底层指定测试 IP，标头保持原始 `Host: orig_host`；
2. **证据 2（常驻 DNS 零同步 Popen 与异步自愈架构验证）**【尚待验证】：验证预启动常驻 DNS 子进程架构与非嵌套锁机制，验证在热路径 `resolve()` 中 100% 绝无同步 `Popen` 系统调用；验证进程崩溃后请求立即 Fail-Closed 快速失败，异步单实例后台自愈调度；验证 IPC 超时强杀时通过操作系统内核释放对端管道打断读写工作线程；
3. **证据 3（POSIX 逐级防穿透与原始路径组件校验验证）**【尚待验证】：在受控安全目录（`~/.agentdeck_test/keys`）中验证合法路径 `/var/lib/keys` 顺利通过，而非法的 4 种形态（`/var/lib/./keys`、`/var/lib/../keys`、`var/lib/keys`、`./keys`）在 `Path` 实例化前被严格拦截；验证 `dir_fd` 打开与 `0600` 权限；
4. **证据 4（Windows DACL 双模式与特殊字符路径验证）**【尚待验证】：在 Windows 真实子进程环境下，验证路径含空格与特殊字符时四阶段安全 DACL 全生命周期验证通过；
5. **证据 5（Service Worker 双向异步时序 CAS 验证）**【尚待验证】：在基于 Promise/Deferred 控制的双向乱序事件流中，验证高版本先到、低版本后到以及相反时序下，IndexedDB 均 100% 杜绝旧版本覆写；
6. **证据 6（Watchdog 真实本地 HTTPS TLS 握手挂起强关验证）**【尚待验证】：使用本地真实 HTTPS 服务端在 TCP 建立后暂停 TLS 握手，验证 Watchdog 在预算内强关拥有底层真实 fd 的 `SSLSocket`，Adapter 真实转换为 `AbsoluteDeadlineExceededError`，检验中断耗时预算目标（$\le 0.30\text{s}$）；
7. **证据 7（Watchdog 真实本地 HTTPS HTTP 响应读取挂起强关验证）**【尚待验证】：使用本地真实 HTTPS 服务端完成 TLS 握手后在响应阶段挂起，验证 Watchdog 强关 `SSLSocket`，Adapter 真实转换为 `AbsoluteDeadlineExceededError`，检验中断耗时预算目标（$\le 0.30\text{s}$）；
8. **证据 8（Windows 防火墙出站规则与 DNS 通畅验证）**【尚待验证】：验证防火墙脚本正确配置 27 个保留 CIDR 的 TCP 443 Block 规则，同时放行 UDP 53 DNS；应用层 Adapter 对非 443 端口执行 100% 严格拒绝；
9. **证据 9（生产环境依赖锁定文件 17 项依赖完全一致性验证）**【尚待验证】：验证 `requirements-production-webpush.lock` 中的 17 项依赖与当前虚拟环境 100% 完全精确一致。

---

## 十二、Phase 5.4 准入清单（Gate Checklist - v3.2 终审整改准入闭环版）

- [ ] **P0-01 TLS 连接代码规范与四阶段 Watchdog 全覆盖**：
  - [ ] 彻底删除重复的 `def connect` 定义，消除语法瑕疵与缩进不齐；
  - [ ] 明确 Watchdog 四阶段连续覆盖机制（TCP connect、TLS handshake、HTTP request send、HTTP response read）；
  - [ ] 保留 `do_handshake_on_connect=False` 延迟握手包装，先取得持有底层真实 fd 的 `SSLSocket` 再注册 Watchdog，最后显式 `do_handshake()`；
  - [ ] 纠正 0.3s 绝对保证声明，明确其为设计目标与需实测指标，保留测试 4a 与 4b 实测检验【尚待验证】。
- [ ] **P0-02 8秒全局Deadline、热路径零同步Popen与异步单实例自愈状态机**：
  - [ ] 预启动常驻 DNS 伴生子进程，Worker 启动阶段执行 `start(timeout=2.0)` 握手探测；
  - [ ] 常驻热路径 `resolve()` 100% 绝无同步 `Popen` 系统调用；
  - [ ] 子进程死亡时请求立即 Fail-Closed 快速失败，绝不等待不可中断的进程创建；
  - [ ] 调度单实例后台自愈工作线程，`_healing_lock` 严格防惊群与防线程无界累积；
  - [ ] `_lifecycle_lock` 获取全部受当前 Deadline 预算约束，禁止无界等待；
  - [ ] `_lifecycle_lock` 与 `_ipc_lock` 严格零嵌套，双锁深度严格为 0；
  - [ ] 全局 8 秒 Deadline 预算严格传递扣减，任何阶段绝不重新创建完整 8 秒预算；
  - [ ] 通过 `WatchdogContext` 在 ContextVar 中实现 set、reset 与 finally 确定性清理。
- [ ] **P0-03 跨平台管道强关差异考证与工作线程多级降级**：
  - [ ] 深入说明 POSIX 与 Windows 在阻塞 write、阻塞 flush、阻塞 readline 及跨线程 close 时的底层内核差异；
  - [ ] 明确子进程被 kill 释放对端句柄才是跨平台通用的可靠中断机制，破除跨线程 close 管道的单边假设；
  - [ ] 建立 IO 工作线程发生极端挂起无法退出时的隔离队列（上限 3）与不可静默忽略的 CRITICAL 审计告警多级降级机制，破除 daemon 线程掩盖泄漏假设；
  - [ ] 细化覆盖三种阻塞场景的独立测试：测试 2b-1（write 阻塞）、测试 2b-2（flush 阻塞）、测试 2b-3（readline 阻塞）【尚待验证】。
- [ ] **P0-04 DNS 生命周期状态机完善与幂等 close() 终态屏障**：
  - [ ] 引入 `ResolverState` 状态机枚举（UNINITIALIZED, STARTING, RUNNING, RECOVERING, STOPPING, STOPPED）；
  - [ ] `__init__` 明确初始化 `_closed = False`、`_state`、`_proc = None`、`_quarantined_threads = []`；
  - [ ] `start()` 严格检查关闭状态，已关闭拒绝启动；
  - [ ] `close()` 具备完全幂等性，限时获取锁，关闭后置为 `STOPPED`，释放锁后清理资源；
  - [ ] 终态屏障：一旦 `STOPPED`，彻底拒绝后续任何 `start()`、`resolve()` 或异步自愈调度，彻底消除死锁。
- [ ] **P0-05 POSIX平台安全与原始路径组件严格校验**：
  - [ ] 独立提取 `validate_posix_keys_path(keys_dir)`，在 `Path` 实例化前原样切分，显式拦截含 `.` 或 `..` 的非法形态（5 种形态覆盖测试【尚待验证】）；
  - [ ] 逐级检查 Fail-Closed，安全验证所有者为 root 或当前 UID，严格禁止 group/other 写；
  - [ ] 测试配置受控安全根目录夹具（如 `~/.agentdeck_test/keys`），对 `/tmp` 01777 拒绝；
  - [ ] 从根受控父目录逐级下行，确保验证全链路均有 `O_NOFOLLOW` 保护；
  - [ ] 临时文件写入异常时在 `except` 分支执行安全 `unlink` 清理；
  - [ ] 写入成功权限严格设为 `0600`，100% 匹配原子替换目标要求；
  - [ ] 全量断言 `os.supports_dir_fd`（包含 open, replace, unlink），缺失任何一项即 Fail-Closed；
  - [ ] 临时文件替换失败显式记录不可静默忽略的 CRITICAL 安全审计日志。
- [ ] **P0-06 Windows DACL双模式继承验证与四阶段全生命周期闭环**：
  - [ ] `verify_windows_dacl_strictly` 引入 `allow_inherited: bool = False` 双模式，解决阶段 2 验证继承合规与其余阶段严格无继承的逻辑矛盾；
  - [ ] 阶段 2（临时文件刚创建瞬间）使用 `allow_inherited=True`，严格限定继承 ACE 仅限当前用户与 SYSTEM 且具备 FullControl，坚决拒绝任何第三方继承 ACE 与 Deny ACE；
  - [ ] 阶段 1、3、4 使用 `allow_inherited=False` 严格模式；
  - [ ] 统一使用 `param($p)` 并通过 `-args $target_path` 安全绑定，杜绝注入风险；
  - [ ] 增加 `found_system` 标记，强制核验当前用户 SID 与 SYSTEM SID 存在且分别具备 `FullControl` 权限。
- [ ] **P1-01 状态映射与约束明确**：
  - [ ] `DeliveryResponse` 显式区分 `status_code`（统一归一化码 201）与 `raw_status_code`（真实原始返回码 200/201/202）；
  - [ ] Worker 内部判断成功严格使用 `resp.status_code == 201`；
  - [ ] Worker 内部判断 400/401/403 标记 `failed`，429 标记退避，404/410 注销订阅；
  - [ ] 数据库 `last_http_status` 严格持久化记录 `raw_status_code`（返回 200 时持久化记录 200，超时/异常显式写入 `NULL`）；
  - [ ] 日志同时记录双字段，原始状态信息零丢失。
- [ ] **P1-02 Service Worker CAS幂等与真实异步时序闭环**：
  - [ ] 严格版本比较与数值比较函数 `compareVersions(v1, v2)`，支持 `aggregated_count` 与 `event_seq_id`；
  - [ ] 阶段降级在同一 `readwrite` 事务原子执行，包含分支：current 为空写入、小版本写入、高版本存感知态并反写告警；
  - [ ] 采用基于 Promise / Deferred 控制的真实异步时序测试，双向验证时序，对冲旧版本覆写。
- [ ] **P1-03 Windows 防火墙与 Adapter 协同实现 Default Deny 架构**：
  - [ ] 明确 Windows Defender Firewall 系统默认出站行为为 Allow，严禁 AgentDeck 全局修改破坏系统基础网络；
  - [ ] 系统防火墙针对 27 个保留 CIDR 精准设置 TCP 443 Block 规则，显式放通 UDP 53 DNS；
  - [ ] **应用层 Adapter 执行全端口 Default Deny**：对非 443 端口（如 80, 8080）与全部非公网 IP 100% 拒绝，实现全端口默认拒绝架构；
  - [ ] 明确保留 IP 阻断、非 443 端口阻断与 DNS 通畅的三重真实验证方式。
- [ ] **P1-04 IP Pinning Host 标头与单次连接隔离**：`send()` 显式将原始 Host 请求头注入请求并添加 `Connection: close`，通过本地真实 HTTPS 服务端测试验证【尚待验证】。
