"""Freeze the 100-case, real-document section retrieval benchmark before running it."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = '''eyelid-swelling|2|眼皮轻微起皮发痒，哪些常见眼睑问题可能自行缓解？
eyelid-swelling|3|眼皮上长个包和眼皮痒脱皮，可能对应的原因有啥不同？
eyelid-swelling|4|眼睑不适时，药师可以提供哪些处理帮助？
eyelid-swelling|5|眼皮不舒服老不好，还长黄斑，这时候该去看医生吗？
eyelid-swelling|6|眼皮红热疼痛并且睁不开眼，属于需要尽快评估的表现吗？
abdominal-pain|1|肚子疼是不是都代表大毛病，有些能不能过几天自己好？
abdominal-pain|2|腹痛伴胀气、便秘或消化不良时，常见原因如何区分？
abdominal-pain|3|肚子不舒服想先问药师，他能帮忙看啥？
abdominal-pain|4|腹痛、腹胀反复出现一直不缓解，应在何时寻求医疗帮助？
abdominal-pain|5|右下腹痛和月经时肚子绞痛，资料里列的可能原因一样吗？
headache|1|多数头痛是否会自行缓解，是否通常代表严重疾病？
headache|2|头疼的时候能做点啥缓解，喝水休息有帮助吗？
headache|3|头痛自行处理后继续加重，或伴视力异常，应如何就医？
headache|4|我老是头疼，是不是跟喝水少、没按时吃饭也有关系？
ankle-swelling|2|久坐久站、吃盐过多或某些药物为什么可能与脚踝水肿有关？
ankle-swelling|4|两边脚脖子肿了好几天，自己处理没用，要找医生了吗？
ankle-swelling|5|单侧脚踝突然肿痛、发红发热，哪些情况需要尽快处理？
ankle-swelling|6|脚肿还胸口发紧、喘不上气，这算不能等的危险情况吗？
ankle-swelling|7|脚踝水肿时抬高腿部、温和活动和鞋袜选择有什么建议？
common-cold|2|感冒一般是慢慢加重还是一下子来，常见表现有哪些？
common-cold|3|普通感冒居家恢复时，休息、补水和饮食有哪些处理建议？
common-cold|4|感冒十多天没见好，咳了好几周，是不是该看医生了？
common-cold|5|感冒患者如何减少通过咳嗽、喷嚏和接触传播给他人？
common-cold|6|家里有人伤风，我咋做能少被传染，共用毛巾行不行？
adult-fever|2|成人通常从多少摄氏度开始被认为发热，正常体温是否固定？
adult-fever|3|我身上发热打寒战，可温度计没到三十八，这能完全排除发烧吗？
adult-fever|4|成人发热不适时，休息、饮水及一般对症处理有哪些建议？
adult-fever|5|大人发烧在家处理了还越来越难受，啥时候不能再拖？
adult-fever|6|成人发热通常与哪些原因有关，是否常见于感冒或流感感染？
pediatric-fever|2|给孩子量体温应该用什么温度计，多少度算发热？
pediatric-fever|3|儿童发热期间如何补水、观察脱水并安排日常照护？
pediatric-fever|4|宝宝不到三个月量到三十八度，或者大点的孩子烧了五天，要赶紧咨询吗？
pediatric-fever|5|儿童发热伴颈部僵硬、不褪色皮疹或首次抽搐，哪些属于急症信号？
pediatric-fever|6|孩子发烧除了咳嗽感冒，也可能是水痘扁桃体炎或者打疫苗引起的吗？
rash|2|荨麻疹的皮疹通常有什么外观、感觉和分布特点？
rash|3|起风团想去药店买抗过敏药，有长期病或者是小孩子要先说明吗？
rash|4|荨麻疹超过两天未改善、反复发作或伴发热时，应如何就医？
rash|5|起疹子同时嘴唇舌头肿了、吞咽费劲，这时候能等药起效吗？
rash|7|食物、冷热刺激、药物或感染可能如何诱发荨麻疹？
knee-pain|2|膝盖疼又肿，先少站一会儿和敷冰袋这些具体怎么做？
knee-pain|3|膝关节疼痛数周仍未改善，是否应寻求医生或康复帮助？
knee-pain|4|膝盖疼到踩不了地还又红又热，哪些表现不能继续硬扛？
knee-pain|5|医生可能为持续膝痛安排哪些药物、物理治疗或进一步检查？
knee-pain|6|运动扭了一下膝盖和反复跑跳后疼，可能的原因有啥区别？
chest-tightness|1|胸痛是否一定由严重疾病引起，为什么仍应寻求医疗建议？
chest-tightness|2|胸口突然一直压着疼，还往胳膊上串、冒冷汗，是否得立刻求助？
chest-tightness|3|胸痛反复出现又消失，即使每次很短是否仍应看医生？
chest-tightness|4|饭后胸口烧、泛酸，和运动拉伤后的胸疼，资料里可能是哪些原因？
chest-tightness|5|心包炎、心绞痛和心肌梗死与胸痛有什么关系？
gout|2|大脚趾突然疼得厉害又红又烫，是痛风常见的表现吗？
gout|3|首次出现疑似痛风或惯用治疗无效时，为什么应接受医生评估？
gout|4|关节突然肿痛还发高烧、恶心，是不是不能只当痛风处理？
gout|5|疑似痛风就诊时，尿酸检测和关节液检查分别可能用于什么？
gout|6|痛风发作时的治疗，和为了少复发而长期吃的药，是一回事吗？
gout|9|为减少痛风复发，体重、饮酒、饮水及运动有哪些调整建议？
chickenpox|1|水痘一般多久能好，所有人得了都不严重吗？
chickenpox|2|水痘的皮疹分哪些阶段，除皮疹外还可能有哪些症状？
chickenpox|3|孩子出水痘很痒，指甲、喝水和返校时间这些在家要咋安排？
chickenpox|4|水痘症状加重或不确定是否水痘时，何时应寻求医疗帮助？
chickenpox|5|水痘还没出疹子也会传人吗，接触水疱液会不会被传染？
migraine|2|偏头痛发作除单侧搏动性头痛外，还可能出现哪些表现？
migraine|4|偏头痛拖了三天多，先兆超过一小时，或者刚生完孩子，这时候怎么办？
migraine|5|头痛突然极其剧烈并伴说话困难或单侧无力，为什么需要紧急处理？
migraine|6|偏头痛的急性缓解治疗跟预防下次发作，有哪些不同路子？
migraine|8|偏头痛发作时暗室休息及日常作息、饮水和压力管理有哪些建议？
sprains|2|脚踝崴了又肿又青，使不上劲，扭伤拉伤通常都啥表现？
sprains|3|扭伤或拉伤后最初两三天，PRICE 处理分别包括哪些措施？
sprains|4|扭伤两周好一些了能马上去跑步吗，严重拉伤会拖多久？
sprains|6|扭伤后疼痛和肿胀加重、不能负重或出现发热，应何时就医？
sprains|9|受伤时听到咔嚓声，现在变形还发麻发凉，是不是可能骨折了？
flu|3|流感常见症状包括哪些，与普通感冒的起病方式有什么区别？
flu|4|得流感发热浑身疼，在家能怎么照顾自己、缓解不舒服？
flu|5|哪些高风险人群出现流感症状应尽快咨询医生？
flu|6|得流感突然胸口疼、喘得说不出话或咳血，是不是得紧急处理？
flu|7|流感患者怎样通过手卫生和咳嗽礼仪减少传播？
sinusitis|2|鼻窦炎是不是常在感冒后，脸颊额头疼、黄绿鼻涕这些算表现吗？
sinusitis|3|轻度鼻窦炎在休息、饮水、避免刺激及盐水清洁鼻腔方面有哪些建议？
sinusitis|5|鼻窦炎自己弄了三周没好，或者药店处理一周没用，要看医生吗？
sinusitis|6|鼻窦炎伴明显全身不适、止痛无效或免疫力低下时，应如何求助？
sinusitis|7|鼻窦炎是不是都得吃抗生素，鼻喷激素和抗过敏药什么时候会考虑？
tonsillitis|2|扁桃体炎在儿童和成人中可能出现哪些症状？
tonsillitis|4|扁桃体发炎嗓子痛，喝凉饮、休息和盐水漱口这些有啥讲究？
tonsillitis|6|扁桃体有脓点或咽痛严重到无法进食饮水时，应如何处理？
tonsillitis|7|看扁桃体发炎，医生为啥有时要拿棉签擦嗓子或者抽血？
tonsillitis|8|病毒性和细菌性扁桃体炎的治疗有什么区别，是否都需要抗生素？
contact-dermatitis|2|碰到东西后手上又痒又起疱还开裂，是接触性皮炎的表现吗？
contact-dermatitis|3|接触性皮炎困扰日常生活时，药师能提供哪些保湿处理建议？
contact-dermatitis|4|皮炎老是反复很严重，又找不到碰了啥导致的，要不要去看皮肤科？
contact-dermatitis|5|刺激性与过敏性接触性皮炎在致病机制上有何不同？
contact-dermatitis|7|工作老接触清洁剂，怎么洗护和防护才能少犯接触性皮炎？
back-pain|2|腰背痛可能由哪些常见损伤或疾病引起？
back-pain|3|腰疼好几周没好，晚上更痛还莫名变瘦，这时候要找医生吗？
back-pain|4|腰背痛突然严重或快速加重，并伴寒战和全身不适，应如何求助？
back-pain|5|腰疼还两条腿都麻、大小便控制不住，这是不是急症信号？
back-pain|6|普通腰背痛的居家护理中，活动、休息与冷热敷有哪些建议？
hypertension|2|没啥感觉的人也可能血压高吗，哪些因素会增加风险？
hypertension|4|高血压通常有哪些症状，为什么不能只凭感觉判断血压？
hypertension|7|胳膊绑袖带测出血压高后，为啥还可能让回家继续量几天？
hypertension|8|长期高血压未控制会增加哪些心脑血管和肾脏风险？
hypertension|9|想靠生活习惯降点血压，吃盐、运动和控制体重都怎么改？'''


def build():
    chunks = json.loads((ROOT / 'rag_data/symptom_references/chunks.json').read_text(encoding='utf-8'))
    by_id = {item['id']: item for item in chunks}
    questions = []
    for number, line in enumerate(SPEC.splitlines(), 1):
        topic, section, question = line.split('|')
        item = by_id[f'nhs-symptom-{topic}-{section}']
        questions.append({
            'id': f'R{number:03}', 'split': 'validation_100', 'topic': topic,
            'kind': 'direct' if number % 2 else 'colloquial', 'question': question,
            'gold': [item['id']], 'source_url': item['source']['url'],
            'section': item['section_path'][-1],
            'gold_text_sha256': hashlib.sha256(item['raw_quote'].encode()).hexdigest(),
        })
    if len(questions) != 100 or len({q['gold'][0] for q in questions}) != 100:
        raise ValueError('Expected 100 questions with 100 distinct gold sections')
    output = ROOT / 'eval_data/real_retrieval_100.json'
    output.write_text(json.dumps({
        'schema_version': 'real-retrieval-100.v1',
        'scope': '真实NHS公开资料上的中文段落检索；Agent编写金标，未经独立医学审核；不代表医学正确率。',
        'provenance': '20篇既有公开资料、100个不同小节；50直接问法、50口语问法。替换旧40题结果；部分来源与旧金标小节复用，因此不是全新盲测。',
        'annotation': '单个精确小节正向金标，不保证穷尽同样相关的其他段落；题目与金标在本轮检索运行前固定。',
        'questions': questions,
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Wrote {len(questions)} cases: {output}')


if __name__ == '__main__':
    build()
