export type ExtractedDrug = { name: string | null; brand: string | null; is_rx: boolean | null;
  ingredients: string | null; contraindications: string | null;
  dosage_form: string | null; specification: string | null };

export function parseOcrResponse(value: unknown): {items: ExtractedDrug[]; truncated: boolean; duplicates: number} {
  if (typeof value !== 'object' || value === null) throw new Error('识别结果格式异常，请手动填写');
  const data = value as Record<string, unknown>;
  if (data.extraction_only !== true || data.needs_confirmation !== true) throw new Error('识别结果缺少确认标记');
  const raw = Array.isArray(data.items) ? data.items : [data];
  if (raw.length > 12) throw new Error('单图最多识别12种药，请分批拍摄');
  const items = raw.map((item: unknown): ExtractedDrug => {
    if (typeof item !== 'object' || item === null) throw new Error('药品预填数据格式异常');
    const drug = item as Record<string, unknown>;
    for (const key of ['name', 'brand', 'ingredients', 'contraindications']) {
      if (drug[key] !== null && typeof drug[key] !== 'string') throw new Error('药品文字字段格式异常');
    }
    if (drug.is_rx !== null && typeof drug.is_rx !== 'boolean') throw new Error('处方属性必须人工核对');
    for (const key of ['dosage_form', 'specification']) {
      if (drug[key] != null && typeof drug[key] !== 'string') throw new Error('药品规格或剂型格式异常');
    }
    return {name: drug.name as string | null, brand: drug.brand as string | null,
      ingredients: drug.ingredients as string | null, contraindications: drug.contraindications as string | null,
      is_rx: drug.is_rx as boolean | null, dosage_form: (drug.dosage_form ?? null) as string | null,
      specification: (drug.specification ?? null) as string | null};
  });
  return {items, truncated: data.truncated === true,
    duplicates: Number.isSafeInteger(data.duplicates_removed) && Number(data.duplicates_removed) > 0 ? Number(data.duplicates_removed) : 0};
}
