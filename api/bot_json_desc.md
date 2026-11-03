# 剧本 JSON 文件格式说明（bot_json_desc）

> 依据：2026-09-07 批量导出的三类剧本 JSON 样本（对话流程编排 flow / 纯PE prompt_only /
> Multi Agents multi_agent）逐一解析；checksum 重算经 `volc_aibot/gojson.calc_checksum`
> 三类样本全部验证一致。
> **修改剧本文件前必须先读本文档理解格式**；checksum 计算细节另见
> `prompt/剧本json文件HASH字段计算规则.md`。

## 0. 文件来源

- 控制台「导出剧本」或 MCP `export_script(script_id)` 下载，文件为 UTF-8 JSON 文本
  （服务端导出文件名即剧本名称，如 `【存客】multi-agent_v2.json`）；
- 修改后经 MCP `import_script(file_path, group_name)` 导入：工具会自动补
  `_由AI修改` 后缀并**重算 checksum**（原文件不动，副本导入）。

## 1. 顶层结构

```
{
  "data": { ...剧本全部内容... },
  "checksum": "64位十六进制字符串（SHA-256，对 data 做 Go encoding/json.Marshal 后哈希）"
}
```

- `checksum` 是**导出文件的完整性校验**：手工修改 `data` 后必须重算，否则导入被拒
  （导入工具已内置重算，手工链路用 `volc_aibot/gojson.py` 的 `calc_checksum(data)`）；
- 三类剧本的顶层结构完全一致，差异全部在 `data` 内部。

## 2. 三类剧本与判别

**唯一判别字段：`data.meta.agent_mode`**（与控制台「剧本类型」一一对应）：

| agent_mode | 控制台剧本类型 | AgentMode 数值 | 样本 |
|---|---|---|---|
| `"flow"` | 对话流程编排 Agent | 1 | T1-10承诺还款剧本 |
| `"prompt_only"` | 纯 PE 型 Agent | 2 | 90+促提纯PE_V2 |
| `"multi_agent"` | Multi Agents | 3 | 【存客】multi-agent_v2 |

`data` 顶层字段按类型的差异（`✓`=有）：

| data 字段 | flow | prompt_only | multi_agent |
|---|---|---|---|
| `meta` | ✓ | ✓ | ✓ |
| `agent_config` | ✓ | ✓ | ✓ |
| `backgroundAudio` | ✓ | ✓ | ✓ |
| `prompt` | ✓ | ✓ | ✓ |
| `vocabulary` | ✓ | ✓ | ✓ |
| `llm_variables` | ✓ | ✓ | ✓ |
| `component`（流程图） | ✓ | — | ✓ |
| `sub_agent_config`（子Agent） | — | — | ✓ |

## 3. data 各字段详解

### 3.1 `meta`——剧本标识（必改字段）

```json
{"agent_mode": "multi_agent", "call_type": "OUT",
 "name": "【存客】multi-agent_v2", "project_id": 3580, "service_id": 47262}
```

- `name`：剧本名称（导入副本的后缀基于它）；`project_id`/`service_id`：导出时归属
  （导入时以目标项目组为准重新分配 service_id，原值仅参考）；
- `call_type` 固定 `"OUT"`（外呼）。

### 3.2 `agent_config`——全局配置（三类共有）

大 dict，常见子键（未配置的服务端字段多为 `null` 或字符串 `"None"`——multi_agent
的 `sub_agent_config` 里用字符串 `"None"`，顶层 `agent_config` 用 `null`，勿互相照搬）：

| 子键 | 含义 | 结构示例 |
|---|---|---|
| `dialog_control_cfg` | 对话控制 | `{HangupTextList, MaxDialogueRounds, MaxModelErrorCount, MaxSilenceCount(, MaxSilenceSecond)}` |
| `answer_recognize_cfg` | 非真人接听识别 | `{IsEnabled: bool, HangupText: str}` |
| `asr_context_cfg` / `asr_vad_value` / `asr_poi_enabled` | ASR 设置 | `{Enable}` / 整数 ms / null |
| `interruption_cfg` | 打断设置 | `{BlacklistWords, BlacklistWordPercent, HangupSupportInterrupt, LimitSecond, ...}` |
| `knowledge_library_cfg` | 知识库挂载 | `{QaLibraries, TopK}` |
| `data_extract_agent_id` / `leads_grading_agent_id` / `dialog_summary_agent_id` | 分析Agent 挂载（BDE/BLG/DSA 前缀 ID） | 字符串 |
| `timbre_cfg` / `timbre_cfgs` / `timbre_speaker_id` / `timbre_type` | 音色 | `{Speed, Volume}` 等 |
| `speech_concatenation_cfg` | 语音拼接 | `{IsEnabled, MinTextLength, TimeIntervalMs}` |
| `e2e_speech_model` / `linking_phrase_cfg` / `pe_mode` / `special_variables_cfg` / `sms_cfg` / `global_variables_cfg` / `high_priority_label_cfg` | 其他 | — |

⚠ `pe_mode`：flow/prompt_only 为 `0`，multi_agent 顶层为 `4`（其 sub_agent_config
内也为 `4`）——与「纯PE」类型无关，是配置形态标记，**不要因类型名改它**。

### 3.3 `backgroundAudio`——背景音（三类共有）

```json
{"background_audio": "<字符串化 JSON 或空串>", "speed": 0, "volume": 0}
```

`background_audio` 是**字符串化 JSON**（见 §4）；未设置时为空串。

### 3.4 `prompt`——提示词列表（类型差异核心）

列表，每元素 `{"information": "<字符串化 JSON>", "mode_type": M, "model_type": "模型名"}`
（multi_agent 的子元素另带 `sub_agent_id`）。

**mode_type 语义**（实测三类样本）：

| mode_type | 出现位置 | information 解析后的内容 |
|---|---|---|
| `1` | flow 的 prompt[0]（唯一元素） | `{BackgroundTheme, RolesTasks, Constraint, Flow, GlobalReply}`（全局提示词五段：背景/角色任务/约束/流程/全局回复，均为长文本） |
| `2` | prompt_only 的 prompt[0]；multi_agent 的 prompt[1:]（带 sub_agent_id） | `{CustomPrompt: 系统提示词全文, Prologue: {ModelType, LLMTextStr, FixedTextStr}}`（开场白三键：模型类型/LLM 生成文本/固定文本） |
| `3` | multi_agent 的 prompt[0]（主） | `"{}"`（空对象占位——主 Agent 无自身提示词，实际内容在各子 Agent 的 mode_type=2 元素里） |

- `model_type`：LLM 模型名（如 `Doubao-Seed-2.0-lite`、`Doubao-Seed-2.0-lite(fast)`）；
- multi_agent 的 `prompt[1:]` 元素与 `sub_agent_config` 通过 `sub_agent_id` 一一对应
  （顺序一致，实测 4 个子 Agent）。

### 3.5 `component`——流程编排图（flow / multi_agent 有；prompt_only 无）

列表（实测均只有 1 个元素），元素结构：

```
{global_information, information, inner_global_information, mode_type,
 name, special_global_nodes, sub_agent_id}
```

- `information`：**字符串化 JSON**——**flow** 为 `{Nodes: [...], Edges: [...]}`（对话流程图）；
  **multi_agent** 为 `{Nodes: [...]}`（子 Agent 跳转图，无 Edges）；
- `global_information`：**字符串化 JSON**——全局应答节点列表（如用户忙/挂机等全局应答，
  flow 实测为节点数组；multi_agent 为子 Agent 级全局应答）；
- `inner_global_information`：字符串化 JSON（内层全局信息，实测多为空串）；
- `special_global_nodes`：特殊全局节点（实测 None/空）。

**flow 的 Nodes/Edges**（对话流程编排的核心）：

```
Node: {ID: "1", Name: "开场白", TypeID: "6", Number: "1",
       Data: {ScriptType: "llm", MaxExecuteTimes, LLMContent, LLMRecommendContent,
              Script: {...话术配置}, CannotInterruptLimitSecond, EnableInterrupt,
              LLMRecommendContentConfig},
       Action: {...}, JumpRules: [...], IsHungup: false,
       VoiceControl: {VadModeValue}, Position: {x, y}, Size: {Height, Width}}
Edge:  {ID: "<uuid>", Source: {Cell: <节点ID>, Port: "<uuid>"}, Target: {Cell: <节点ID>}}
```

- `TypeID`/`ScriptType`：实测全部节点 TypeID=6、ScriptType="llm"（LLM 话术节点）；
- `JumpRules`（节点级跳转）：`[{PortID: "<uuid>", CorrectionType: 1, LLMContent: "<跳转条件描述>",
  NextNodeID: "<目标节点ID>"}]`——与 Edge 的 Port 对应；
- `Data.Script`：话术细节 dict（含分支话术数组）；
- 数值/布尔为原生 JSON 类型（`"ID"`/`"TypeID"` 是字符串，`IsHungup` 是 bool）。

**multi_agent 的 Nodes**（子 Agent 跳转图）：

```
Node: {SubAgentID: "<sub_agent_id>",
       JumpRules: [{PortID: "<uuid>", RuleName: "跳转1", LLMContent: "<跳转条件>",
                    NextSubAgent: {SubAgentID: "<目标子Agent>"}}]}
```

### 3.6 `sub_agent_config`——子 Agent 配置（仅 multi_agent）

列表，每元素一个子 Agent 的配置：

```
{sub_agent_id: "cbdahj", pe_mode: 4, ...与顶层 agent_config 同名的配置子键...}
```

⚠ 未设置的配置项在 sub_agent_config 里是**字符串 `"None"`**（不是 null）；
`sub_agent_id` 与 `prompt[1:]` 的 `sub_agent_id`、component Nodes 的 `SubAgentID` 三处一致。

### 3.7 `vocabulary`——变量定义（三类共有）

```json
[{"is_required": true, "key": "agent_id", "name": "座席工号",
  "value_type": "text", "variable_type": "String"}]
```

- 即控制台「全局变量」的定义列表（含名称/调用名称/必填/类型）；
- `value_type`："text"；`variable_type`："String"/"Integer"/"Float"/"Boolean"（字符串形式）；
- 注意与接口 `llm/global_variables` 的字段名不同（那边是 `IsRequired`/`VariableType` 数值）。

### 3.8 `llm_variables`——变量取值快照（三类共有）

```
{online_config: "<字符串化 JSON 数组>", preview_config: "<字符串化 JSON 数组>"}
```

解析后（两个字段都要 `json.loads`）：`[{key, value, name, is_required, variable_type}]`
——导出时刻的**线上/测试两套变量取值**（`variable_type` 此处是数值 1/2/3/4，
与 vocabulary 的字符串形式不同，勿混淆）。

## 4. 字符串化 JSON 字段清单（修改必读）

以下字段的值是 **JSON 字符串**（外层看是 string，内容是 JSON 文本），修改时必须
「解析 → 改内容 → 重新序列化为字符串 → 再算整体 checksum」：

| 字段 | 三类均有 | 内容 |
|---|---|---|
| `prompt[*].information` | ✓ | 提示词主体（见 §3.4） |
| `llm_variables.online_config` | ✓ | 线上变量取值数组 |
| `llm_variables.preview_config` | ✓ | 测试变量取值数组 |
| `backgroundAudio.background_audio` | ✓ | 背景音配置 |
| `component[0].information` | flow/multi | 流程图 Nodes/Edges |
| `component[0].global_information` | flow/multi | 全局应答节点 |
| `component[0].inner_global_information` | flow/multi | 内层全局信息 |

**重新序列化格式必须与平台一致**（详见 `prompt/剧本json文件HASH字段计算规则.md`）：

- 紧凑分隔符（`","` / `":"`，无空格）；
- 中文原样输出（ensure_ascii=False）；
- **保持原键序**（不排序——与外层 marshal 的键排序规则不同）。

验证方法：不改内容，解码再编码，重算 checksum 应与原文件完全一致
（`calc_checksum` 已在三类样本上验证通过）。

## 5. 修改流程（要点）

1. `export_script` 导出原文件 → 复制一份工作副本（勿改原文件）；
2. 需要改嵌套字段（§4 清单）时：`json.loads` 解析 → 修改 → 按原格式规则重新
   `json.dumps`（紧凑/中文原样/保持键序）写回字符串；
3. 改顶层字段直接改；
4. **重算 checksum**：`from volc_aibot.gojson import calc_checksum;
   new_checksum = calc_checksum(data)`，写回 `checksum` 字段；
5. `import_script(file_path, group_name)` 导入（自动补 `_由AI修改` 后缀并重算
   checksum，第 4 步可省）；导入后回查（`search_scripts`/`query_script`）确认。

⚠ 常见坑：
- 只改了 data 忘了重算 checksum → 导入失败；
- 嵌套字符串字段直接当字符串拼接修改 → 内容格式破坏 + checksum 永远对不上；
- multi_agent 三处 `sub_agent_id`（prompt/component/sub_agent_config）需保持一致；
- `pe_mode` 不要按类型名改（§3.2）；
- `variable_type` 在 vocabulary（字符串）与 llm_variables（数值）两处形态不同。
