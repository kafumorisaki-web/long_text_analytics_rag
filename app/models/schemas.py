from pydantic import BaseModel, Field, create_model, model_validator
from typing import List, Optional, Literal, Type
from enum import Enum
from types import GenericAlias

"""
reader01的JSON格式限制
"""


def get_reader01_dynamic_schema(valid_entity_keys: List[str], custom_properties_config: dict) -> Type[BaseModel]:
    """
    根据 JSON 配置的实体白名单和自定义属性， Reader01 的 Pydantic 输出骨架。
    """
    # 动态生成实体类别枚举
    if not valid_entity_keys:
        DynamicEntityEnum = str
    else:
        DynamicEntityEnum = Enum('DynamicEntityEnum', {e: e for e in valid_entity_keys})

    # 动态生成提取实体的基础结构 (系统硬编码部分)
    DynamicExtractedEntity = create_model(
        'DynamicExtractedEntity',
        raw_name=(str, Field(..., description="实体原名")),
        type=(DynamicEntityEnum, Field(..., description="实体类型")),
        local_summary=(str, Field(..., description="本段实体简介，不多于20字。"))
    )

    fields = {
        "segment_summary": (str, Field(..., description="本段落剧情/事实总结。LLM推演的惯性导航垫片，必须保留。")),
        "extracted_entities": (List[DynamicExtractedEntity],
                               Field(default_factory=list, description="本段落主要实体清单"))
    }

    for prop_key, prop_val in custom_properties_config.items():
        # 提取配置字典中的所有约束
        data_type_str = prop_val.get("data_type", "str")
        desc = prop_val.get("description", "无描述")
        max_length = prop_val.get("max_length")
        default_val = prop_val.get("default")

        field_kwargs = {"description": desc}

        # 拦截最大长度
        if max_length is not None:
            field_kwargs["max_length"] = int(max_length)

        # 默认值与必填判定
        default_arg = default_val if default_val is not None else ...

        # 严格类型映射
        if data_type_str == "int":
            py_type = int
        elif data_type_str == "list[str]":
            py_type = List[str]
        elif data_type_str == "enum":
            options = prop_val.get("options", [])
            if not options:
                raise ValueError(f"致命错误：Reader01 自定义字段 '{prop_key}' 被标记为 enum，但缺失 options 数组。")
            # 动态生成枚举类防碰撞
            enum_name = f"Reader01{prop_key.capitalize()}Enum"
            py_type = Enum(enum_name, {str(opt): str(opt) for opt in options})
        else:
            py_type = str

        fields[prop_key] = (py_type, Field(default_arg, **field_kwargs))

    return create_model('DynamicSegmentPayload', **fields)


def get_macro_dynamic_schema_partial(target_properties: dict):
    fields = {}

    # 循环遍历要生成的归纳目标字段 (tgt_key)
    for tgt_key, prop_val in target_properties.items():

        data_type_str = prop_val.get("data_type", "str")
        desc = prop_val.get("description", "无描述")
        max_length = prop_val.get("max_length")
        default_val = prop_val.get("default")

        field_kwargs = {"description": desc}

        if max_length is not None:
            field_kwargs["max_length"] = int(max_length)

        default_arg = default_val if default_val is not None else ...

        if data_type_str == "int":
            py_type = int
        elif data_type_str == "list[str]":
            py_type = List[str]
        elif data_type_str == "enum":
            # 提取 enum 必须的 options 数组
            options = prop_val.get("options", [])
            if not options:
                raise ValueError(f"归纳字段 '{tgt_key}' 被标记为 enum，但未提供 options 数组。")

            enum_name = f"{tgt_key.capitalize()}Enum"
            py_type = Enum(enum_name, {str(opt): str(opt) for opt in options})
        else:
            py_type = str

        fields[tgt_key] = (py_type, Field(default_arg, **field_kwargs))

    return create_model('DynamicMacroPartialPayload', **fields)


"""
reader02的JSON格式限制
"""


def _build_optional_delta_field(prop_key: str, prop_val: dict, enum_prefix: str) -> tuple:
    data_type_str = prop_val.get("data_type", "str")
    desc = prop_val.get("description", "")
    max_length = prop_val.get("max_length")
    default_val = prop_val.get("default", None)
    if default_val == "":
        default_val = None

    field_kwargs = {"description": desc}

    if data_type_str == "int":
        py_type = int
    elif data_type_str == "list[str]":
        py_type = List[str]
        if max_length is not None:
            field_kwargs["max_length"] = int(max_length)
    elif data_type_str == "enum":
        options = prop_val.get("options", [])
        if not options:
            raise ValueError(f"Reader02 配置错误：字段 '{prop_key}' 类型为 enum，但未提供有效的 options 列表！")
        enum_name = f"{enum_prefix}_{prop_key.capitalize()}Enum"
        py_type = Enum(enum_name, {str(opt): str(opt) for opt in options})
    else:
        py_type = str
        if max_length is not None:
            field_kwargs["max_length"] = int(max_length)

    return (py_type | None, Field(default=default_val, **field_kwargs))


def get_reader02_state_dynamic_schema(entity_classes_config: dict) -> Type[BaseModel]:
    top_level_fields = {}

    for ent_key, ent_cfg in entity_classes_config.items():
        state_props = ent_cfg.get("state_properties", {})

        delta_fields = {
            "entity_name": (
                str,
                Field(..., description=f"发生状态突变的【{ent_key}】实体标准名称（必须严格出自本段活跃实体名单）")
            )
        }

        for prop_key, prop_val in state_props.items():
            delta_fields[prop_key] = _build_optional_delta_field(
                prop_key=prop_key,
                prop_val=prop_val,
                enum_prefix=ent_key
            )

        # 动态生成专属的 XXXStateDelta 类（例如 CompanyStateDelta、CharacterStateDelta）
        sub_model_name = f"{ent_key}StateDelta"
        SubDeltaModel = create_model(sub_model_name, **delta_fields)

        top_level_fields[ent_key] = (
            GenericAlias(list, (SubDeltaModel,)),
            Field(
                default_factory=list,
                description=f"本段落内状态属性发生实质性突变的【{ent_key}】实体列表；若无任何突变则留空列表 []"
            )
        )

    return create_model("StateUpdateOutput", **top_level_fields)


def get_reader02_relation_dynamic_schema(custom_relations_config: dict) -> Type[BaseModel]:
    top_level_fields = {}

    for rel_key, rel_cfg in custom_relations_config.items():
        allowed_sources = rel_cfg.get("allowed_sources", [])
        allowed_targets = rel_cfg.get("allowed_targets", [])
        rel_props = rel_cfg.get("relation_properties", {})

        if allowed_sources:
            SourceTypeEnum = Enum(f"{rel_key}SourceTypeEnum", {s: s for s in allowed_sources})
        else:
            SourceTypeEnum = str

        if allowed_targets:
            TargetTypeEnum = Enum(f"{rel_key}TargetTypeEnum", {t: t for t in allowed_targets})
        else:
            TargetTypeEnum = str

        delta_fields = {
            "source_entity": (str, Field(..., description=f"【{rel_key}】关系发起方实体标准名称")),
            "source_type": (SourceTypeEnum, Field(..., description=f"发起方实体类别，严格限定为: {allowed_sources}")),
            "target_entity": (str, Field(..., description=f"【{rel_key}】关系接收方实体标准名称")),
            "target_type": (TargetTypeEnum, Field(..., description=f"接收方实体类别，严格限定为: {allowed_targets}"))
        }

        for prop_key, prop_val in rel_props.items():
            delta_fields[prop_key] = _build_optional_delta_field(
                prop_key=prop_key,
                prop_val=prop_val,
                enum_prefix=rel_key
            )

        sub_model_name = f"{rel_key}Delta"
        SubRelDeltaModel = create_model(sub_model_name, **delta_fields)

        top_level_fields[rel_key] = (
            GenericAlias(list, (SubRelDeltaModel,)),
            Field(
                default_factory=list,
                description=f"本段落内发生新建或突变的【{rel_key}】交互关系列表（发起方 {allowed_sources} -> 接收方 {allowed_targets}）；若无变化则留空列表 []"
            )
        )

    return create_model("RelationUpdateOutput", **top_level_fields)


"""
reader03的JSON格式限制
"""


def get_reader03_dynamic_schema(valid_entities: List[str], custom_payload_fields: dict, min_chunks: int,
                                max_chunks: int) -> Type[BaseModel]:
    if not valid_entities:
        DynamicEntityEnum = str
    else:
        DynamicEntityEnum = Enum('DynamicEntityEnum', {e: e for e in valid_entities})

    chunk_fields = {
        "start_paragraph_id": (int, Field(
            ...,
            description="本切片在当前脉络节点中的起始物理自然段段号，必须从 1 开始且严格递增。"
        )),
        "entities": (Optional[List[DynamicEntityEnum]], Field(
            default_factory=list,
            description="从给定的合法实体枚举中，选出本切片实际出场的实体。"
        ))
    }

    for prop_key, prop_val in custom_payload_fields.items():
        data_type_str = prop_val.get("data_type", "str")
        desc = prop_val.get("description", "")
        max_length = prop_val.get("max_length")
        default_val = prop_val.get("default")

        field_kwargs = {"description": desc}
        if max_length is not None:
            field_kwargs["max_length"] = int(max_length)

        default_arg = default_val if default_val is not None else ...

        # 类型映射分发
        if data_type_str == "int":
            py_type = int
        elif data_type_str == "list[str]":
            py_type = List[str]
        elif data_type_str == "enum":
            options = prop_val.get("options", [])
            if not options:
                raise ValueError(f"Reader03 切片属性 '{prop_key}' 类型为 enum，但未提供 options 约束")
            enum_name = f"Reader03{prop_key.capitalize()}Enum"
            py_type = Enum(enum_name, {str(opt): str(opt) for opt in options})
        else:
            py_type = str

        # 防止 list 类型缺失 default_factory
        if data_type_str == "list[str]" and default_val is None:
            chunk_fields[prop_key] = (py_type, Field(default_factory=list, **field_kwargs))
        else:
            chunk_fields[prop_key] = (py_type, Field(default_arg, **field_kwargs))

    DynamicChunkMeta = create_model('ChunkMeta', **chunk_fields)

    DynamicSplitSchema = create_model(
        'DynamicSegmentSplitOutput',
        chunks=(
            GenericAlias(list, (DynamicChunkMeta,)), Field(
            ...,
            min_length=min_chunks,
            max_length=max_chunks,
            description=f"切片数组，长度必须在 {min_chunks} 到 {max_chunks} 之间。"
        )
        )
    )
    return DynamicSplitSchema


class AnalyzerOutput(BaseModel):
    thought: str = Field(description="内部推理过程，分析用户意图")
    response: str = Field(description="给用户的最终文本回复")
    # 可以考虑加上各种action: Literal["call_tools", "reply_user", "loop_to_reader"]等
