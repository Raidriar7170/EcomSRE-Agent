# EcomSRE v0.5 — Docker 稳定性核对与一次新 Campaign 准入

> 继续 `Raidriar7170/EcomSRE-Agent` 的同一个 Draft PR #104。  
> 审阅起点：`176d2b782ae8615561c4e7f94347be434d05ba32`。  
> 这是原 Goal 的小范围续跑补充，不是新版本、另一个安全框架或新增预算。  
> 本次只核对了仓库结果和启动脚本，没有在用户本机复现 bridge 变化。

## 0. 用户激活文字

保存、阅读本文不构成执行或修改 Docker 设置的授权。由用户在原 Codex 会话发送：

```text
继续 PR #104，执行 docs/goals/EcomSRE_v0.5_Docker_Stability_Resume.md。
先核对 Docker Desktop Resource Saver、相关生命周期证据和当前资源状态。
需要修改 Docker Desktop 设置时由我操作，不自动重启、重置或修改系统配置。

我授权：在旧 campaign 的 owned 资源已清理、未发现额外非项目对象漂移、
并完成本文稳定性检查后，为一个新的 campaign 建立一次独立预检基线。
这是有条件接受新实验起点，不是确认我曾手动重启 Docker，
也不是确认旧 bridge 变化已经查明或没有影响。
旧 BLOCKED_SAFETY、clean=false 和全部证据保持不变；
新实验中再次漂移仍立即停止，不自动再次更新基线。

满足准入条件后，继续原 Goal 的首个真实 Discovery episode及后续学习验收。
保持已工作的 Provider、原累计账本、Runtime 检查和只读恢复边界。
不自动 merge/release，不重跑旧冻结研究。
```

这项显式授权只解除“必须回溯查明旧 bridge 变化原因才能考虑任何新实验”的无限期前置条件；
不解除当前环境稳定性、资源所有权或新运行中的漂移检查。

## 1. 已证实的起点，与尚未证实的解释

仓库 `docs/results/product-v050/live-resume/result.json` / `README.md` 记录：[R1][R2]

- 创建 22 个容器、1 个项目网络和 5 个项目卷；启动容器为 0，故障注入为 0。
- default `bridge` 在前置观测、prestart、cleanup 后的 ID/Created 发生变化。
- 三次 Created：`2026-09-18T02:40:45Z`、`02:47:02Z`、`02:53:41Z`。
- 配置投影摘要相同，daemon ID 相同；这些不是“进程连续运行未重启”的证明。
- 所有 28 个 owned 资源的移除都有记录；owned remaining 为 0/0/0。
- 原有三个卷在已记录的比较范围内未变化；不能外推为未做过的完整数据校验。
- `non_owned_unchanged=false`，`clean=false`，历史终态为 `BLOCKED_SAFETY`。
- 网络事件未取得，原因 UNKNOWN；没有证据支持把变化归因给用户或某一进程。
- 本轮新增 Provider 请求及独立 live episode 均为 0。

优先检查的假设是 Docker Desktop Resource Saver。官方文档说明，未运行容器达到空闲时间后，
该功能会停止 Linux VM；默认时间为 5 分钟，某些只读 Docker 命令不一定唤醒或保持 VM 运行。[D1]
本轮一直没有运行中的项目容器，两个创建时间间隔约 6 分钟，值得核对该解释。
**时间吻合与功能开启都不能独自证明历史原因。**
Docker 默认 bridge 与 daemon 初始化有关；不得从一般机制直接推导本次一定发生了某种重启。[D2]

## 2. 先做有限只读排查，不再创建 22 个容器尝试碰运气

复用已有日志、inspect 和资源比较代码；优先不改业务源码。

1. 读取最新 HEAD、原账本、旧 attempt 的私有元数据、精确清理回执及差异报告。
2. 核对当前 Docker context、实际 endpoint、daemon 信息和项目残留；不自动切换 context。
3. 检查 Docker Desktop Resource Saver 当前是否开启、idle timeout、可取得的 VM/engine 生命周期信息。
   优先用 UI，由用户确认当前设置。确需程序读取时，只读取 Resource Saver 相关设置项。
4. 在已存在的 Docker Desktop 本地诊断中，有限检索旧时间区间前后各几分钟的 idle、pause、resume、
   VM stop/start、engine restart 或 crash 线索。路径和版本先确认，不扫描整个 home、不访问凭据。
5. 如查不到历史日志，写 `HISTORICAL_CAUSE_UNDETERMINED`，不伪造用户确认，不无限调查。
6. 核对新脚本和调用记录是否涉及非项目网络操作或 context 切换；发现越界则不能准入新 campaign。

只读排查指不直接提交配置或资源写操作；Docker Desktop 可能因查询自行唤醒 VM。
该自动行为也应记录，不宣称排查绝不改变 VM 生命周期。
原始 Docker inspect、设置和日志不公开提交，只保留必要的脱敏事实、时间与差异。
不索取完整设置文件，不上传诊断包，不输出环境变量或 Docker/Provider 凭据。

## 3. 稳定实验环境：设置由用户控制

若 Resource Saver 已开启，向用户说明：可在本轮实验期间，临时关闭 Docker Desktop
`Settings → Resources → Resource Saver`，结束后按用户原偏好恢复。[D1]

- Codex 不自动修改 Docker Desktop 设置文件，不自动重启 daemon/VM，不执行 factory reset。
- 不修改 DNS、代理、证书、防火墙、Docker 数据目录或资源配额来绕过此检查。
- 不运行任意“保活容器”来规避 Resource Saver，不删除默认 bridge，不使用 global prune。
- 不要求用户为了继续实验谎称“当时手动重启过”。用户不记得可以记 UNKNOWN。
- 设置本来已关闭时，不归因 Resource Saver；继续按有限日志与当前稳定性证据判断。
- 如临时关闭需要用户点击 Apply/restart，先确认没有会被打断的其他工作，且在新基线采集前完成。
- 实验阶段避免手动重启 Docker、切换 context 或让主机睡眠；不能保证时保留阻塞。

暂停节能只用于降低再次发生生命周期切换的可能，不构成旧因果归因或安全证明。

## 4. 新基线准入：一次、有条件、不可覆盖历史

以下各项满足之前，不创建新 campaign 资源、不做故障注入或 Provider 调查：

### 4.1 核对旧资源与差异

- 从旧出生记录重新核对全部 owned 资源已不存在；标签/身份不明确不能删除。
- 核对原有非项目卷、容器、网络；不得仅因名字相同就认为对象未变。
- 旧差异必须能限定在已记录的默认 bridge 身份/Created 变化。
  出现额外非项目删除、配置变化、挂载/连接变化、context/endpoint 不一致或权限异常，停止并报告。
- 配置摘要相同只能说明该投影相同；列明投影实际检查了哪些字段，不能当作整个主机无变化证明。
- 未执行数据内容检查时，不声称“所有原有数据完全没变”。

### 4.2 当前稳定性检查

在用户完成所需设置调整、Docker 已就绪之后，进行一次约 10 分钟的有界稳定性观察。
这是本次工程采样要求，不是数学或安全保证。建议每 30 秒记录一次：

```text
UTC timestamp / monotonic elapsed
context 与 endpoint 是否匹配
可取得的 VM/engine 生命周期线索
bridge 的 Id / Created / Driver / Scope / IPAM / Options / Attachments 等选定字段
既有非项目对象身份、配置的必要投影
旧 owned 资源残留
```

历史事件监听与当前采样复用现有工具，明确覆盖窗口与缺失项；空事件列表不证明没有事件。
不通过密集 ping、反复创建/移除容器制造“稳定”，不把查询当作可靠的保活方式。

若期间出现新的身份或语义漂移，结束这次观察并保留阻塞，不自动滑动窗口到最后一次变化后重新计时。
若有非项目工作必须同时改变该环境，不在原 strict 实验合同里假装全环境不变；另行讨论实验隔离。

### 4.3 一次性接纳新起点

只有 4.1、4.2 通过且用户已发送第 0 节的条件授权，才：

- 用新 campaign nonce 和新目录记录新的 preflight；关联旧 BLOCKED 记录，但不覆盖它。
- 旧 `clean=false`、非项目漂移、unknown 原因和旧账本不变。
- 新基线是“从现在开始比较”的实验起点，不是旧环境已经恢复原样的证明。
- 不删除历史 hash、出生记录或失败 marker，不给旧 campaign 发一个新的通过结果。
- 必须使用新路径，例如独立的 `live-02`；当前 `live_environment.py` 的 ROOT 需先检查。
  若还硬编码 `live-01`，仅增加显式受限的新 campaign 路径支持及聚焦测试，绝不覆盖旧目录。[R3]
- 新 create/start 前后仍核对同一份新基线。任何后续漂移仍阻塞；本文不授权第二次自动换基线。
- 新运行的身份、配置、健康基线与证据重新绑定，不能拿旧失败环境的快照代替。

即使历史 Resource Saver 解释仍未证实，用户可在这些条件下接受一个新的实验起点；
这与把历史未知结果改为成功是两回事。

## 5. 恢复原 v0.5 主线，不新增范围

通过准入后，在原有最小依赖、缓存固定镜像及 owned 本地控制范围内推进：

```text
新 campaign → readiness / 新健康基线
→ 首个真实独立 Discovery episode
→ LLM 调查与实际补查证据
→ 原计划多事件候选提议、开发检查
→ 冻结独立验证 → 条件式测试晋升
→ 新事件正常 Product 入口零 LLM 复用
```

原有确定性规则、证据窗口、负证据、候选依赖、Shadow 与隔离检查不降级。
不强制 LLM 产生或晋升无效规则。不开放 Product LLM 的 Docker socket、shell 或恢复写权限。
实验控制器权限与 Product 权限继续分离；不新建通用 Agent、安全审批服务器或另一套存储。

Provider 保持已工作配置，除非用户另行明确更改。不重复旧生成排障。
累计总上限仍为 200 请求、USD 20 成本/承诺上界、12 live episode；
起点记录为 24 请求、USD 0.232264 承诺、0 episode，执行时核对原账本。
新 campaign 不是新额度，prestart 失败也不冒充已完成独立事故。

## 6. 防止再次陷入无效续跑

- 新漂移、未知所有权或其他真实安全问题：立即停相关外部动作；只做已授权、身份可证的清理。
- 不因单次正常阶段结束再次索取已明确的同一授权。
- 没有新证据，不循环 resume 同一失败 campaign，不反复写“工程已完成”报告或跑全量测试。
- 只改实验路径/小范围适配时先跑聚焦回归；有稳定交付 HEAD 后才做必要的全量 CI。
- 本轮只追加一份简短记录：历史解释及不确定性、用户设置动作（如有）、当前稳定观察、
  新基线准入/拒绝、首个 episode 实际结果、累计预算与准确清理状态。
- 沿用同一 Draft PR。无新增用户指令不 merge/tag/release/deploy。

## 7. 核对资料

- [R1] 当前机器结果：`https://github.com/Raidriar7170/EcomSRE-Agent/blob/176d2b782ae8615561c4e7f94347be434d05ba32/docs/results/product-v050/live-resume/result.json`
- [R2] 当前结果说明：`https://github.com/Raidriar7170/EcomSRE-Agent/blob/176d2b782ae8615561c4e7f94347be434d05ba32/docs/results/product-v050/live-resume/README.md`
- [R3] 当前启动适配：`https://github.com/Raidriar7170/EcomSRE-Agent/blob/176d2b782ae8615561c4e7f94347be434d05ba32/scripts/product_v050/live_environment.py`
- [D1] Docker Resource Saver：`https://docs.docker.com/desktop/use-desktop/resource-saver/`
- [D2] Docker bridge：`https://docs.docker.com/engine/network/drivers/bridge/`

资料解释通用机制，不证明本机开启过该功能或本次历史变化的成因。
