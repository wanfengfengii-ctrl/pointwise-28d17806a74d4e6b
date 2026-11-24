# 恒川双测道波形采集站

材料疲劳试验室一号台架按工单记录 A、B 两道的分段波形。操作员登记录制布局，推进板卡录制与通知，必要时安排一次补拍。质检员按成果号领取固定波形、绝对峰值、逐段出处和成功回执，按原登记顺序导出交班表。

## 运行

需要 Python 3.11 和随附标准库。在项目根目录运行：

```text
python3 -B check_project.py
python3 -B -m station demo
python3 -B -m station init station.sqlite3
```

控制台提供 `check_project.py`，用它运行 `tests` 中的完整回归。`demo` 读取 `samples/first.json`、`samples/retry.json` 和 `samples/shift.json`，在临时站库中真实录制、签发、关闭录制源，再重开领取和交班。也可用 `demo --samples samples` 指定编辑后的录制目录。`first.json` 的通知顺序引用实写产生的通知列表，索引从零开始，末次实写产生一条段通知及一条完成通知。`retry.json` 的两拍布局相同。`shift.json` 指定领取顺序及一个只实写首段的工单。初始化可以重复执行，保留原登记、超时、签件和成功回执，并延续登记序号。样例只在显式运行演示时读取。

## Python 接口

用 `station.Station(database, areas_per_lane=2)` 打开站库。站内主机事件逐个受理。每道的接收区数量可配置为一个，以便现场只有原区可复用时等待。`Station` 支持上下文管理器。

| 方法 | 输入和结果 |
| --- | --- |
| `open(shift, ticket_id, lane, spec)` | 登记非空班次与工单号，返回持有 `first` 尝试和 `state` 的工单。`lane` 取 `A` 或 `B`。冲突工单号及无效输入拒绝。接收区暂满时，同道排队。 |
| `event(notification)` | 消费一条板卡通知，返回采集进展，完整时含实收。未签发的实收可从 `station.capture.receipts` 查找。重复及错身份通知无进展。 |
| `expire_first(ticket_id)` | 在首拍尚未被完整接管时记录超时并请求停止，返回 `True`。其他时机返回 `False`。 |
| `redo(ticket_id)` | 给已超时工单安排唯一补拍。返回 `RetryStatus.SCHEDULED` 或 `WAITING_FOR_AREA`。两者均占补拍名额，`ticket.retry_attempt` 保存身份。其他状态拒绝补拍。 |
| `seal(ticket_id, receipt_id)` | 对该工单当前唯一可签的完整实收一次性提交签件和成功回执。缺实收、无资格和重复封存拒绝。SQLite 失败会回滚两条新增记录，并允许在本会话使用同一实收重试。 |
| `get(artifact_id)` | 原样领取固定签件及成功回执。未知成果号抛出 `MissingArtifact`。 |
| `export(shift, registrations)` | 输入 `station.registrations(shift)` 中所需的原登记项，返回稳定 JSON。混班或不匹配原登记的清单拒绝。待处理项的 `artifact_id` 和 `receipt_id` 为空。 |

`device.descriptors.RecordingSpec` 接受按原序排列的 `SegmentSpec(segment_id, position, sample_count)`。位置从零连续编号，段标识在布局内唯一，每段至少一个样本。样本是有符号 64 位整数微应变，拒绝布尔值、浮点值、越界值和长度错误。峰值是全波形逐点取绝对值后的最大整数，负向最小样本可产生 `2**63` 的峰值。出处保存实际测道、工单与尝试号、接收区、原段身份、半开样本区间，以及该尝试对应的板卡实写事实引用。

板卡由 `station.board.write_segment(attempt, position, samples)` 推动实写，返回通知。选择通知的引用调用 `board.deliver_event(reference)`，再交给 `station.event`。延后这个调用可重现乱序或迟到。可通过 `board.trace.entries()` 查看已发生的动作。超时和停止请求都允许已接受的尝试继续实写。`board.confirm_stop(attempt)` 解除对应尝试的写入权并生成通知，采集接收确认后安全归还缺段尝试的接收区。自然完成时，接收区保留到主机收到结束通知及全部分段证据。任一测道等待期间另一道可推进。

主机先完整接管首拍时，后至的超时保持首拍可签。超时先成立时，迟到的首拍完整实收可供当前会话核对，签发资格留给补拍。签件与成功回执保存在站库，关源后按原成果号读取。交班使用固定样本复算峰值，核对一份实收的原段覆盖和回执关联。重开后的未签工单仍可列在交班表，旧录制会话中的未签实收和设备写入会话随会话结束。

## 首拍用例

```python
from capture.receipts import receipt_id_for
from device.descriptors import RecordingSpec, SegmentSpec
from station import Station

layout = RecordingSpec((SegmentSpec('head', 0, 1), SegmentSpec('tail', 1, 1)))
with Station('station.sqlite3') as station:
    work = station.open('day', 'sample-01', 'A', layout)
    notices = (*station.board.write_segment(work.first, 0, [-7]),
               *station.board.write_segment(work.first, 1, [4]))
    for notice in reversed(notices):
        station.event(station.board.deliver_event(notice.reference))
    artifact, receipt = station.seal('sample-01', receipt_id_for(work.first))
    print(artifact.samples, artifact.peak, receipt.receipt_id)
```

这个录制的原序为 `(-7, 4)`，两点绝对值分别为 `7` 和 `4`，因此固定峰值为 `7`。交班表仍按登记顺序排列，冻结样例中补拍工单的峰值为 `13`，B 道独立工单为 `5`，缺第二段的工单仍待处理。
