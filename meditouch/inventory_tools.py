"""Read-only pharmacy tools. Bind one store's engine on the host, never in model arguments."""
from langchain_core.tools import tool
from sqlalchemy import or_
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from meditouch.inventory_models import DrugDetails, DrugInventory


# Candidate lookup only. Not a treatment protocol or a special-population clearance.
# Adult fever reference: https://www.nhs.uk/symptoms/fever-in-adults/
DEFAULT_SYMPTOM_TERMS = {
    '发烧': ('布洛芬', '对乙酰氨基酚'),
    '发热': ('布洛芬', '对乙酰氨基酚'),
}
MAX_RESULTS = 50


def build_medical_rag_tool(*, model=None, service=None):
    @tool(parse_docstring=True)
    def query_medical_rag(symptoms: list[str], customer_tags: list[str],
                          excluded_drugs: list[str] | None = None,
                          medical_topics: list[str] | None = None) -> dict:
        """检索医学资料生成待查库存药名；无支持资料时可使用明确标记的模型知识兜底。

        结果不保证同效、没有禁忌或适合顾客；替代候选必须重新查询产品并经过安全核查。
        不返回剂量、处方、货架或库存，不得以此批准联合用药。

        Args:
            symptoms: 已归一化且明确肯定的症状列表。
            customer_tags: 顾客明确报告的人群或疾病标签。
            excluded_drugs: 此前因禁忌、缺货或无法匹配而排除的药名。
            medical_topics: 用户明确咨询的疾病或健康主题，仅作检索，不代表已确诊。

        Returns:
            recommended_drugs、source、evidence 和 status；source 区分 retrieval 与 llm_knowledge。
        """
        from meditouch.medical_candidates import query_candidates
        return query_candidates(symptoms, customer_tags, excluded_drugs or [], model=model, service=service,
                                medical_topics=medical_topics or [])
    return query_medical_rag


query_medical_rag = build_medical_rag_tool()


def search_inventory_batch(engine, names, *, excluded_drugs=()):
    """One parameterized OR query; preserve per-term identity and ambiguity rather than pick an SKU."""
    names = list(dict.fromkeys(_text(name) for name in names))
    if not names or len(names) > 12:
        return {'status': 'invalid_input', 'items': []}
    statement = select(DrugInventory, DrugDetails).outerjoin(
        DrugDetails, DrugDetails.inventory_id == DrugInventory.id).where(or_(*[_name_filter(name) for name in names]))
    if excluded_drugs:
        statement = statement.where(DrugInventory.name.not_in(tuple(excluded_drugs)))
    try:
        with Session(engine) as session:
            rows = session.exec(statement.order_by(DrugInventory.id).limit(MAX_RESULTS + 1)).all()
            items = []
            for drug, details in rows[:MAX_RESULTS]:
                item = _item(drug, details)
                item['ingredients'] = details.ingredients if details else None
                item['matched_terms'] = [name for name in names if name.lower() in
                                         (drug.name + ' ' + (drug.brand or '')).lower()]
                item['name_match'] = ('exact' if drug.name in names else 'brand_exact'
                                      if drug.brand and drug.brand.casefold() in {name.casefold() for name in names}
                                      else 'fuzzy')
                items.append(item)
        truncated = len(rows) > MAX_RESULTS
        records = {}
        for name in names:
            matched = [item for item in items if name in item['matched_terms']]
            records[name] = {'term': name, 'items': matched, 'status': 'matched' if matched else
                             'unconfirmed' if truncated or excluded_drugs else 'not_found'}
        return {'status': 'ok' if items else 'not_found', 'items': items,
                'truncated': truncated, 'query_records': records,
                'unmatched_terms': [name for name in names if not any(name in item['matched_terms'] for item in items)]}
    except SQLAlchemyError:
        return {'status': 'database_unavailable', 'items': [], 'query_records': {
            name: {'term': name, 'items': [], 'status': 'database_unavailable'} for name in names}}


def _text(value, limit=200):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError('请输入非空且长度有效的文本')
    return value.strip()


def _name_filter(name):
    # Escape LIKE wildcards: OCR text containing % or _ must not match all stock.
    return or_(DrugInventory.name.contains(name, autoescape=True),
               DrugInventory.brand.contains(name, autoescape=True))


def _item(drug, details):
    result = {
        'inventory_id': drug.id, 'name': drug.name, 'brand': drug.brand,
        'shelf_location': drug.shelf_location, 'stock': drug.stock, 'is_rx': drug.is_rx,
        'contraindications': details.contraindications if details else None,
        'ingredients': details.ingredients if details else None,
        'dosage_form': details.dosage_form if details else None,
        'specification': details.specification if details else None,
        'status': 'out_of_stock' if drug.stock == 0 else 'in_stock',
    }
    if drug.is_rx:
        result['warning'] = '处方药需药师审核'
    return result


def build_inventory_tools(engine, *, symptom_terms=None, excluded_drugs=(), exact_names=False):
    """Bind an authorized single-store engine. This factory does not create tables.

    Pass a host-maintained symptom-to-ingredient/name dictionary to override the
    small demonstration mapping. Neither engine nor mapping is exposed to the LLM.
    Every invocation opens its own session and only executes SELECT statements.
    """
    source = DEFAULT_SYMPTOM_TERMS if symptom_terms is None else symptom_terms
    if not isinstance(source, dict) or any(
        not isinstance(key, str) or not key.strip()
        or not isinstance(values, (list, tuple)) or not values
        or any(not isinstance(v, str) or not v.strip() for v in values)
        for key, values in source.items()
    ):
        raise ValueError('症状映射必须为非空文本到药名或成分列表的字典')
    mapping = {key.strip(): tuple(v.strip() for v in values) for key, values in source.items()}
    excluded = tuple(excluded_drugs)

    @tool(parse_docstring=True)
    def search_inventory(symptom: str | None = None, drug_name: str | None = None) -> dict:
        """只读查询当前已绑定门店的药品、货架和库存，返回候选商品而非用药推荐。

        药名对 name 与 brand 做字面子串匹配；症状通过宿主维护的词典映射，
        再匹配药名或详情 ingredients。两个参数同时存在时取交集。
        不支持的症状返回 unsupported_symptom，不会列出全店商品。
        宿主可绑定完整名称精确模式，此时不匹配品牌或相似复方名，模型不能切换该模式。
        每条商品含 inventory_id、name、brand、shelf_location、stock、is_rx、
        contraindications、剂型与规格；零库存标为 out_of_stock，处方药附药师审核提示。
        最多返回50条，truncated=true 时必须缩小药名/品牌范围，不能声称已查全。
        禁忌空白表示未知；is_rx=false 只是录入值，不代表已核实非处方属性。
        结果不代表顾客适用、已完成安全核查或已获售药批准。

        Args:
            symptom: 可选的症状标准词，如“发烧”；至少与 drug_name 提供一个。
            drug_name: 可选的通用名或品牌片段，如“布洛芬”或“芬必得”。

        Returns:
            可 JSON 序列化字典，含 status、items、truncated；异常时不泄露数据库信息。
        """
        try:
            symptom, drug_name = _text(symptom), _text(drug_name)
            if symptom is None and drug_name is None:
                return {'status': 'invalid_input', 'reason': '请提供症状或药名', 'items': []}
            statement = select(DrugInventory, DrugDetails).outerjoin(
                DrugDetails, DrugDetails.inventory_id == DrugInventory.id)
            if excluded:
                statement = statement.where(DrugInventory.name.not_in(excluded))
            if drug_name:
                statement = statement.where(DrugInventory.name == drug_name if exact_names else _name_filter(drug_name))
            if symptom:
                terms = mapping.get(symptom)
                if not terms:
                    return {'status': 'unsupported_symptom', 'items': [],
                            'reason': '该症状尚无找药映射，请提供药名或咨询药师'}
                statement = statement.where(or_(*[
                    or_(DrugInventory.name.contains(term, autoescape=True),
                        DrugDetails.ingredients.contains(term, autoescape=True)) for term in terms
                ]))
            with Session(engine) as session:
                rows = session.exec(statement.order_by(DrugInventory.id).limit(MAX_RESULTS + 1)).all()
                items = [_item(drug, details) for drug, details in rows[:MAX_RESULTS]]
            return {'status': 'ok' if items else 'not_found', 'items': items,
                    'truncated': len(rows) > MAX_RESULTS, 'scope': '候选库存查询，不代表适用或允许售药'}
        except ValueError:
            return {'status': 'invalid_input', 'items': [], 'reason': '参数为空或长度超限'}
        except SQLAlchemyError:
            return {'status': 'database_unavailable', 'items': [], 'reason': '库存查询暂不可用，请人工核对'}

    @tool(parse_docstring=True)
    def check_drug_safety(drug_name: str, customer_tags: list[str]) -> dict:
        """隔离核对顾客标签与某一药品已录入的禁忌简写；只做文本风险筛查。

        优先按完整药名或品牌精确定位，无精确结果时做字面子串匹配。
        若仍匹配多条商品（包括同名不同规格），返回 unknown 和候选，要求澄清，
        绝不自动选第一条。同一名称无法唯一定位时需要宿主后续提供 SKU 选择能力。
        标签出现在非空禁忌文本中时返回 is_safe=false，并保留原始禁忌作为 reason。
        缺少详情、禁忌为空、没有标签、未命中标签、未找到商品及数据库故障均返回
        is_safe="unknown"。不会返回 true：简写未命中不能证明安全。
        该工具不能核验药物相互作用、剂量、年龄细分或复杂否定/条件语义；
        命中需药师复核，unknown 不得解释为可以销售。不能替代完整说明书与药师判断。

        Args:
            drug_name: 实习生已确认的完整药名或品牌，尽量避免宽泛片段。
            customer_tags: 顾客明确报告的标签，如 ["孕妇", "高血压"]；不能推测或补造。

        Returns:
            可 JSON 序列化字典，is_safe 仅为 false 或 "unknown"；含 reason 和核查状态。
        """
        unknown = {'is_safe': 'unknown', 'reason': '缺乏禁忌数据，建议咨询药师'}
        try:
            drug_name = _text(drug_name)
            if drug_name is None or len(customer_tags) > 20:
                return {**unknown, 'status': 'invalid_input'}
            tags = list(dict.fromkeys(_text(tag, 50) for tag in customer_tags))
            statement = select(DrugInventory, DrugDetails).outerjoin(
                DrugDetails, DrugDetails.inventory_id == DrugInventory.id)
            with Session(engine) as session:
                rows = session.exec(statement.where(or_(DrugInventory.name == drug_name,
                                                        DrugInventory.brand == drug_name)).limit(2)).all()
                if not rows:
                    rows = session.exec(statement.where(_name_filter(drug_name)).limit(2)).all()
                if not rows:
                    return {'is_safe': 'unknown', 'status': 'not_found', 'reason': '未找到药品，请核对药名'}
                if len(rows) > 1:
                    return {'is_safe': 'unknown', 'status': 'ambiguous',
                            'reason': '匹配多个商品，请确认完整药名、品牌和规格后交药师核对',
                            'candidates': [_item(drug, detail) for drug, detail in rows]}
                drug, details = rows[0]
                raw = (details.contraindications or '').strip() if details else ''
                identity = {'inventory_id': drug.id, 'name': drug.name}
                if not raw:
                    return {**unknown, **identity, 'status': 'missing_data'}
                if not tags:
                    return {**identity, 'is_safe': 'unknown', 'status': 'missing_tags',
                            'reason': '缺少顾客标签，建议咨询药师'}
                matched = [tag for tag in tags if tag in raw]
                if matched:
                    return {**identity, 'is_safe': False, 'status': 'contraindication_match',
                            'reason': raw, 'matched_tags': matched}
                return {**identity, 'is_safe': 'unknown', 'status': 'no_match',
                        'reason': '已录入禁忌未命中顾客标签，不能证明安全，建议咨询药师'}
        except (ValueError, TypeError):
            return {**unknown, 'status': 'invalid_input'}
        except SQLAlchemyError:
            return {'is_safe': 'unknown', 'status': 'database_unavailable',
                    'reason': '核查暂不可用，建议咨询药师'}

    return search_inventory, check_drug_safety
