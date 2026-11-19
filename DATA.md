# 资料约定

原始资料均为本地合成。CSV 使用 UTF-8，首行是字段名。

所有时段为同一天的当地分钟，区间左闭右开。`duties` 的 from_min 到 until_min 是事项持有人须能履行保管的时段；handoff_by 给允许的最迟接管分钟。`availability` 的 site 是可以执行交接的位置；`transfer_offers` 记双方有共同可用地点的联络时段，`mins` 是交接需连续共同可用的时长，`supervisor` 非空时也须在同地点有空且具备 supervisor 资格。`qualifications` 对给定 duty_type 生效，交接需双方在全过程合格。`locks` 记原已确认或执行中的排他承诺；同一 person 在重叠时段不能同时完成两项排他的承诺。`receipts` 以 side 和 state 分别记录本人确认及接管事实，确认对应 duty 或备用方案编号。`proposals` 记录具体事项、候选联络及方案确认截止时间；issued 表示正在等待回复，adopted 表示已接管。`scenarios` 可禁用人员、资格或具体值班段，明细用 origin_ref 指向原记录 ID。
