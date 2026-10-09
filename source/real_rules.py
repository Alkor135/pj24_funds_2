"""Ограниченный поиск понятных правил покупки, продажи и пропуска дня.

Логика: не более 102 условий задаются квантилями обучения. Отбор направлений
проходит на отдельном следующем участке с расходами 3 б.п. и стрессовым
сценарием. Факты теста не используются в отборе. Конфликт двух сигналов даёт
пропуск. Модуль пакета source используется корневым скриптом research_real.py.

Примеры из папки проекта:
    python research_real.py --cost-bps 3
    python research_real.py --min-train-trades 60 --min-validation-trades 30
"""

from __future__ import annotations

from dataclasses import dataclass

from .real_data import STATES, ResearchConfig, eligible
from .real_stats import quantile_cuts, strategy_metrics


@dataclass(frozen=True)
class Rule:
    """Правило id направления side с перевесом, границей Флэт или наблюдаемым режимом."""
    id: str
    side: int
    relation: str | None = None
    threshold: float | None = None
    flat_cap: float | None = None
    state: str | None = None


def rule_label(rule: Rule | None) -> str:
    """Возвращает русское описание rule; для None — отсутствие выбранного правила."""
    if rule is None:
        return 'Правило не отобрано'
    clauses = []
    if rule.relation:
        clauses.append(f'Рост − Падение {rule.relation} {rule.threshold*100:.4f} п.п.')
    if rule.flat_cap is not None:
        clauses.append(f'Флэт ≤ {rule.flat_cap*100:.4f}%')
    if rule.state:
        clauses.append(f'наблюдаемый режим: {rule.state}')
    return ('Покупка' if rule.side==1 else 'Продажа')+' при '+' и '.join(clauses)


def rule_signals(records, rule: Rule | None, config: ResearchConfig) -> list[int]:
    """Возвращает причинные сигналы records по rule/config без чтения фактов; None — нули."""
    signals = []
    for row in records:
        active = rule is not None and eligible(row,config,rule.state is not None)
        if active and rule.relation:
            edge = row.features.get('edge_direction')
            active = edge is not None and (edge>=rule.threshold if rule.relation=='>=' else edge<=rule.threshold)
        if active and rule.flat_cap is not None:
            flat = row.features.get('p_flat')
            active = flat is not None and flat<=rule.flat_cap
        if active and rule.state:
            active = row.state==rule.state
        signals.append(rule.side if active else 0)
    return signals


def combined_signals(records, long_rule, short_rule, config) -> list[int]:
    """Возвращает общие сигналы records/config; при конфликте long_rule/short_rule — ноль."""
    longs = rule_signals(records,long_rule,config)
    shorts = rule_signals(records,short_rule,config)
    return [0 if a and b else a+b for a,b in zip(longs,shorts)]


def generate_rules(train, config: ResearchConfig) -> list[Rule]:
    """Возвращает не более 102 кандидатов по квантилям признаков train, пригодных по config."""
    rows = [r for r in train if eligible(r,config)]
    thresholds = quantile_cuts([r.features.get('edge_direction') for r in rows])
    flats = quantile_cuts([r.features.get('p_flat') for r in rows],(0.5,0.75))
    rules = []
    for side in (1,-1):
        for relation in ('>=','<='):
            for t in thresholds:
                for cap,state in [(None,None),*((f,None) for f in flats),*((None,s) for s in STATES)]:
                    rules.append(Rule(f'R{len(rules)+1:03d}',side,relation,t,cap,state))
        for state in STATES:
            rules.append(Rule(f'R{len(rules)+1:03d}',side,state=state))
    return rules


def select_rules(train, validation, config: ResearchConfig) -> dict:
    """Возвращает два выбранных правила и аудит кандидатов по train/validation/config; тест не принимает."""
    candidates = generate_rules(train,config)
    audit = []
    accepted = {1:[],-1:[]}
    for rule in candidates:
        train_mask = [eligible(r,config,rule.state is not None) for r in train]
        validation_mask = [eligible(r,config,rule.state is not None) for r in validation]
        training = strategy_metrics(train,rule_signals(train,rule,config),config.cost_bps,train_mask)
        signals = rule_signals(validation,rule,config)
        selection = strategy_metrics(validation,signals,config.cost_bps,validation_mask)
        stress = strategy_metrics(validation,signals,config.stress_cost,validation_mask)
        admitted = (training['trades']>=config.min_train_trades and training['mean'] is not None and training['mean']>0
                    and selection['trades']>=config.min_validation_trades and selection['mean'] is not None and selection['mean']>0
                    and stress['mean'] is not None and stress['mean']>0)
        entry = {'rule':rule,'label':rule_label(rule),'train':training,'validation':selection,
                 'stress':stress,'admitted':admitted}
        audit.append(entry)
        if admitted:
            accepted[rule.side].append(entry)
    selected = {}
    for side,key in ((1,'long'),(-1,'short')):
        ranked = sorted(accepted[side],key=lambda a:(-a['validation']['mean'],-a['validation']['trades'],
                        int(a['rule'].flat_cap is not None)+int(a['rule'].state is not None),a['rule'].id))
        selected[key] = ranked[0]['rule'] if ranked else None
    return {**selected,'candidates':audit,'trials':len(candidates)}
