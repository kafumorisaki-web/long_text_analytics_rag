全局字段说明：

1. 带有 [系统硬编码] 的字段，代表图谱/向量库物理层面的必须字段，UI前端应禁止用户修改。
2. 带有 [用户自定义] 的字段，其内部定义的属性完全遵从 Pydantic 的 Field 参数规范：

- data_type: 对应 Python 数据类型 (如 "str", "int", "list[str]", "enum")，必填
- description: 对应 Field(description=...)，直接喂给大模型作为提取指导，强烈建议填写
- max_length: 对应 Field(max_length=...)，限制大模型生成的字符串最大长度
- default: 对应 Field(default=...)，默认值
- options: 当 data_type 为 "enum" 时必填，限制输出范围

一、 归纳节点 (Macro Nodes)

1. [系统硬编码]
    * macro_id
    * macro_name
    * type
2. [用户自定义] 归纳节点的额外属性，从所包含的脉络节点中归纳出来，格式为：{脉络节点某属性：{归纳节点所需归纳属性名：{描述},...},...}
    * custom_properties:
        * fieldA_from_segment_nodes(字段名可改), 必须归纳出至少一个属性
            * 小说示例:
              {
              "segment_summary":
              {
              "narrative_pace": {"data_type": "str", "description": "分析本卷的冲突密度与叙事节奏分布", "max_length":
              200},
              "plot_summary":{"data_type": "str", "description": "全卷剧情主线脉络总览", "max_length": 300}
              },
              "social_background_snippets":
              {
              "social_background": {"data_type": "str", "description": "全卷所体现的宏观时代社会背景与环境设定", "
              max_length": 200}
              }
              }
            * 财报示例: {"segment_summary":{"quarter_summary": {"data_type": "str", "description": "
              总结本季度的核心营收变化与风险", "max_length": 300}}}
3. 用户预先划分好的实例范围
    * instances：
        * 小说示例: {"macro_id": 1, "macro_name": "初入江湖", "segment_range": [1, 106]}
        * 财报示例: {"macro_id": 1, "macro_name": "2023财年上半年", "segment_range": [1, 6]}

二、 文档结构与脉络节点 (segment_nodes)

1. 文档解析规则
    * parsing_rules
        * segment_business_name：(用于注入 Prompt)
            * 小说示例: "章"
            * 财报示例: "月度"
        * split_pattern：
            * 小说示例：(第[零一二三四五六七八九十百千万0-9]+[章节部分][^\n]*)
        * title_clean_bracket_pattern：
            * 示例：[\(（]+.*?(?:[\)）]|$)
        * title_clean_prefix_pattern：
            * 小说示例：^第[\d零一二三四五六七八九十百千万]+[章节部分][\s\d]*[:：\s]*
        * entity_name_clean_pattern：
            * 示例：[^\w\u4e00-\u9fa5]
2. 脉络节点 Schema
    * [系统硬编码]
        * segment_id
        * segment_name
        * type
        * segment_summary
    * [用户自定义] Reader01 除了 summary 外，还需要顺手从原文里提取哪些元数据？
    * custom_properties
        * 小说示例:
            * "environment_snippets": {"data_type": "list[str]", "description": "0-2句本段环境描写原文"},
            * "scenes": {"data_type": "list[str]", "description": "本章包含的时空组合（时间|空间）"},
            * "social_background_snippets": {"data_type": "list[str]","description": "
              本段明确提及的社会背景、阶层格局、物理/武学法则等世界观设定原文。1-2句话。"},
        * 财报示例:
            * "key_metrics_mentioned": {"data_type": "list[str]", "description": "本段落提及的核心财务指标名称"}

三、 实体与状态节点 (entity_system)

1. [系统硬编码] 以下是所有实体蓝节点必然存在的字段，绝对纯净，不包含任何业务属性
    * base_node_schema
        * name
        * type
2. [用户自定义] 实体分类及其状态演进 Schema (全量平铺，无嵌套)
    * entity_classes（可不断增加下一层级）
        * EntityClassA(字段名可改)
            * 小说示例: "Character"
            * 财报示例: "Company"
        * extraction_description：告诉 Reader01 提取哨兵：什么是 EntityClassA？只有说明白了，大模型才知道要从文本里捞什么。
            * 请在这里填写该类实体的定义。例如：自然人实体、角色、涉案人员等。
        * state_properties：状态节点 Schema：告诉 Reader02，这个实体随着时间推移，想看到哪些属性的变化？
            * 小说示例:
                * "appearance": {"data_type": "str", "description": "外观...", "max_length": 50},
                * "behavior_habits": {"data_type": "str", "description": "行为习惯的抽象总结...", "max_length": 60},
                * "behavior_habits_examples": {"data_type": "str", "description": "行为习惯对应的原文摘抄...", "
                  max_length": 100}
            * 财报示例:
                * "revenue_status": {"data_type": "str", "description": "营收状态...", "max_length": 50}
        * EntityClassB(字段名可改)
            * 小说示例: "Faction"
            * 财报示例: "Product"

四、 关系连线系统 (Relationships)

1. [系统硬编码] 时空拓扑与降维连线，用户不可修改业务属性
    * hardcoded_topologies
2. [用户自定义] 实体交互连线 (原 RELATION)
    * custom_relations（可不断增加下一层级）
        * RelationGroupA(字段名可改)
            * 小说示例: "SocialRelations"
        * allowed_sources：关系的发起实体（关系是有方向的）
        * allowed_targets：关系的目标实体（关系是有方向的）
        * relation_properties：[用户自定义属性]
            * 小说示例:
              "meta_type": {"data_type": "enum", "options": ["POSITIVE", "NEGATIVE", "NEUTRAL"], "description": "
              基本倾向..."},
              "intensity": {"data_type": "enum", "options": ["MARGINAL", "SIGNIFICANT", "PROFOUND"], "description": "
              关系深度..."}
        * RelationGroupB(字段名可改)
            * 小说示例: "AssetRelations" (物品->人物)

五、 切片向量系统 (vector_system)

1. 负载元数据 (payload_schema)
    * [系统硬编码] 向量数据库必备主键与生肉
        * segment_id
        * chunk_index
        * text
        * type
    * [用户自定义] 切片额外元数据
    * custom_payload_fields
        * 小说示例："plot_summary":{"data_type":"str","description":"该切片的核心剧情概括（15字以内）。","max_length":30}

2. 向量化策略 (vector_strategy)
    * [系统硬编码]
        * text_vector
    * [用户自定义] Meta Vector 的合成
        * meta_vector_components: 把 Payload 里的哪些字段抽出来，拼接成一段大白话，去生成 meta_vector
          向量。除“entities”外，这里的元素必须在 custom_payload_fields 列表中存在.
            * 小说示例: ["entities","plot_summary"]