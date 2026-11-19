# 公物仓成套调剂的需求替代与节支认定资料契约

以下为项目本地合成资料，不代表真实居民、机构、金额或政策。所有数据为 UTF-8 JSON Lines，每行是一个完整对象；金额采用整数分，null 表示未知或不适用，不能自动等同于零。时间戳含 +08:00 偏移，日期为当地业务日。known_at 或 reported_at 控制资料何时可得，业务发生时间不意味着当时已经获知。

## 业务含义

需求quantities给出必需组件，未允许splittable时必须整体在deadline前到齐；priority只定义优先级比较，不强制唯一目标函数。同用途compatibility为已确认的型号配对，未列配对为未知，不能默认兼容；冷却用途aircon按检测确认适配，资料不足保留待检测。占用日期均含until_day，随后return_check_days和transport_days都不可供另一需求使用。采购可比价对应整组需求，不允许把不拆分需求的comparable_cents按随意比例分给已交部分。共享trip_cents只计算一次，分摊可选但必须说明依据且各方合计等于实际整组费用。后续交接文件是资料，不表示应用已经接受了对应计划。

## 原始记录

### assets

| 字段 | 含义 |
| --- | --- |
| id | 实物身份 |
| kind | 资产类别 |
| model | 型号 |
| state | 资料所知状态 |
| available_day | 最早释放日 |
| condition_score | 检测得分，未知为空 |
| repair_cents | 已报价维修费 |
| repair_days | 维修所需日数 |
| location | 存放地 |

### needs

| 字段 | 含义 |
| --- | --- |
| id | 需求身份 |
| department | 接收部门 |
| usage | 用途 |
| quantities | 成套必需件数 |
| splittable | 是否允许分期逐项交付 |
| start_day | 使用起日 |
| end_day | 使用止日含当天 |
| priority | 数字越小优先级越高 |
| deadline | 最晚到齐日 |
| procurement_id | 采购批准引用 |

### compatibility

| 字段 | 含义 |
| --- | --- |
| id | 兼容声明 |
| usage | 用途 |
| left_model | 设备型号 |
| right_model | 配套型号 |
| compatible | 已确认能否搭配 |
| known_at | 依据到达时间 |

### occupancies

| 字段 | 含义 |
| --- | --- |
| id | 占用身份 |
| asset | 实物 |
| department | 现用部门 |
| until_day | 使用至该日 |
| return_check_days | 返还后检测日数 |
| transport_days | 运输日数 |

### procurement

| 字段 | 含义 |
| --- | --- |
| id | 采购批准 |
| need | 被批准需求 |
| approved | 是否已获准新购 |
| comparable_cents | 对应整组可比新购价 |
| delivery_cents | 必要交付安装费 |
| shared_trip | 共用搬运计费组 |
| trip_cents | 整组一次搬运总费用 |

### handover

| 字段 | 含义 |
| --- | --- |
| id | 交接补充身份 |
| asset | 实物 |
| known_at | 获知时间 |
| kind | 交接事实 |
| need | 需求 |
| note | 业务说明 |

