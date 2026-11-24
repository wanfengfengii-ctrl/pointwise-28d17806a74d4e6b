# 一次加载的双工单共录

疲劳加载结束后无法补取同一次的原段。共录让两个工单各取本次加载的一段连续窗口，各单仍按自己的首拍、超时和补拍办理。

## 调用

`Station.open_shared(shift, shared_id, lane, spec, windows)` 沿用原班次、测道及 `RecordingSpec`。`shared_id` 为非空字符串，在同站库唯一。`windows` 是含两个新工单号的字典，值为从零计的原段位置半开区间 `(start, stop)`。两窗均非空，互相重叠，并集覆盖全布局。无效输入或冲突以 `ValueError` 拒绝，两单均不登记。成功返回 `(shared_id, tickets)`，其中 `tickets` 是以工单号为键的原 `Ticket` 对象字典，登记顺序沿 `windows` 的迭代顺序。同道无空区时返回字符串 `busy`，不新增登记或板卡录制。

共录用 `station.board.write_segment(shared_id, position, samples)` 实写，`position` 是物理布局中的原段位置。沿 `board.deliver_event(reference)` 和 `station.event(notification)` 投递设备通知。原 `board.state` 和 `board.confirm_stop` 也接受共录号，用于查写权和生成真实停写确认。板卡仍用原轨迹回查实写引用。普通录制继续使用原 `Attempt` 参数，主机仍逐个受理操作和通知。

窗口接管后可用 `station.capture.receipts.for_attempt(ticket.first)` 查其实收，按原 `seal(ticket_id, receipt_id)` 签发。首拍资格按各工单的主机受理顺序确定。已接管的工单不再接受首拍超时。超时成立的工单可用原 `redo(ticket_id)` 排自己的唯一补拍，补拍布局只含该窗口原段，段序从零起，仍用 `ticket.retry_attempt` 推动普通板卡录制。受理超时后，只要还有有效窗欠证，共录就继续。所有窗口已接管或超时而板卡仍能写时请求停写。物理终止可以先于段通知，接收区要等主机受理真实终止并结清所有窗口才能复用。同道补拍等可用区，另一道照常推进。

按 `get(artifact_id)` 原样领取签件和首执。共录首拍的 `artifact.segments` 中，`position`、`start`、`stop` 分别是工单内的段序及半开样本区间，`area_id` 和 `reference` 对应实际实写。逐段可读 `shared_id` 和 `source_position`，分别为共录号和原布局段位置。普通录制和独立补拍沿用实际尝试出处。`export` 继续按原登记顺序核对并呈现已签或待处理工单。断源重开仍可领取已签件和查班表，未签实收仅保留在原会话。

## 可重放样例

`shared_fixture.py` 的 `shared_input()` 提供 `open_shared` 的位置参数。`issue(station, shared_id, position)` 只让板卡实写给定原段，`deliver(station, *notifications)` 可按任意给定次序转交通知。夹具的独立运行展示普通首拍，用来核对输入和通知工具。

样例只给 A 道一个接收区。首段是 `[7,3]`，公共段是 `[11]`，末段是 `[-17]`。X 取 `[0,2)`，Y 取 `[1,3)`。先实写首段和公共段，扣住公共段通知，令 X 超时并排补拍。原触发仍能实写末段。先投递自然终止和末段通知，Y 仍待证，A 的补拍仍等区，B 道可以独立签发。投递公共段通知后，Y 可签出 `[11,-17]`、峰值 17，X 首拍仍失格，补拍可开始。区复用后旧通知重投不改变新录制。

另一轮先投递首段和公共段的通知，末段尚未实写时 X 可独立签出 `[7,3,11]`。若 Y 随后正常采完，两份签件的公共段有同一共录号、原段位置 1 和实写引用。该段在 X 的样本区间是 `[2,3)`，在 Y 是 `[0,1)`。若 Y 改为超时，须等实际停写确认被主机受理后才可复用区。若把公共段通知放在自然终止和两条独占段通知之后，它可同时使两窗齐证，后续复用不能改变任一实收。

沿已有 `.pointwise-runtime.json` 的运行入口保留原回归，核验窗口资格、通知错绑、区复用和签发失败后的同实收续签，失败时退出非零。演示先签一窗、另一窗继续采，停源重开后领取两份成果并核对共享实写和原班表。运行入口沿用清单现有的参数数组格式。
