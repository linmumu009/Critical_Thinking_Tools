"""Deterministic identifiability examples, never empirical LLM results."""
import json
import math

def sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x))

def scenario(name, bias, relation):
    pa=sigmoid(bias+relation)
    return dict(name=name,answer_probability=pa,raw_margin=bias+relation,
                reference_margin=bias,calibrated_margin=relation,
                matched_context_difference=2*relation)

def run():
    cases=[scenario('before',0,2),scenario('answer_bias_only',-4,2),
           scenario('relation_damage_only',0,0.5)]
    assert cases[0]['answer_probability'] > cases[1]['answer_probability']
    assert cases[0]['matched_context_difference'] == cases[1]['matched_context_difference']
    assert cases[2]['matched_context_difference'] < cases[0]['matched_context_difference']
    # A protected coordinate does not constrain an independent held-out coordinate.
    before=[1,1]; after=[1,-1]
    assert before[0]==after[0] and before[1] != after[1]
    # A change of representation coordinates can break a frozen linear readout.
    points=[(1,1),(2,-1),(-1,1),(-2,-1)]
    labels=[1,1,0,0]
    predict=lambda points,w:[int(sum(a*b for a,b in zip(p,w))>0) for p in points]
    accuracy=lambda y:sum(a==b for a,b in zip(y,labels))/len(labels)
    rotated=[(-x,-y) for x,y in points]
    initial=accuracy(predict(points,(1,0)))
    frozen=accuracy(predict(rotated,(1,0)))
    aligned=accuracy(predict([(-x,-y) for x,y in rotated],(1,0)))
    collapsed=accuracy(predict([(0,0) for _ in points],(1,0)))
    assert (initial,frozen,aligned,collapsed)==(1,0,1,0.5)
    return dict(status='design_check_only',real_model_evidence=False,
      C01=dict(cases=cases,interpretation='已知加性logit模型下，答案偏好可改变原始margin而不改关系差分；校正是否改善真实跨问法排序仍未回答。',counterexample='词项偏好也可能真实损害生成可用性；消去它可能掩盖服务能力退化。'),
      C02=dict(before=before,after=after,protected_probe_unchanged=True,heldout_coordinate_damaged=True,interpretation='只证明保护有限probe不逻辑蕴含持出事实保持，不能预测实际迁移效果。'),
      C03=dict(initial_accuracy=initial,frozen_after_rotation=frozen,aligned_accuracy=aligned,collapsed_accuracy=collapsed,interpretation='只证明无标签坐标对应可恢复旋转失配；非线性漂移或真实信息损失未被解决。'))

if __name__=='__main__':
    print(json.dumps(run(),ensure_ascii=False,indent=2))
