"""v1.1 arithmetic for given contributions; not a world or opponent simulator."""


def after_tick(previous, attack_damage=0, kill_awards=0, deaths=0, death_penalty=60):
    # Clamp once, after ALL batches and deaths in the tick. Never clamp at 500.
    return max(0.0, previous + attack_damage + kill_awards - death_penalty * deaths)


def combat_points(damage, cap=500):
    return 15.0 * min(max(0.0, damage) / cap, 1.0)


def marginal_value(previous, gain=0, deaths=0, cap=500, death_penalty=60, tie_weight=0):
    final = after_tick(previous, gain, deaths=deaths, death_penalty=death_penalty)
    # Extra damage has only tiebreak utility after the points cap; losing damage
    # above the cap still costs that utility. This is a policy weight, not points.
    extra = max(0.0, final - cap) - max(0.0, previous - cap)
    return combat_points(final, cap) - combat_points(previous, cap) + extra * tie_weight


# Standard normal quantiles at the midpoints of 8 equal-probability bins.
QUANTILES = (-1.534, -0.887, -0.489, -0.157, 0.157, 0.489, 0.887, 1.534)


def end_value(current, future, spread, **kw):
    """marginal_value judged at the end of the match: the robot is projected to add
    `future` +- `spread` more damage anyway, so a gain or a death now is worth only what
    it changes in the final, capped score."""
    ends = [current + max(0.0, future + spread * z) for z in QUANTILES]
    return sum(marginal_value(end, **kw) for end in ends) / len(ends)


def damage_value(frame, config, gain=0, deaths=0, tie_weight=0.009):
    """Final-score points of `gain` damage (or `deaths`) now. Future own damage is this
    match's rate, shrunk towards config.damage_rate_prior with the weight of 100 ticks;
    the spread is one hit plus 30 % rate uncertainty."""
    current = frame.score.get("effective_damage", 0)
    rate = (current + config.damage_rate_prior * 100) / (frame.tick + 100)
    future = rate * max(0, config.horizon - frame.tick)
    return end_value(current, future, config.attack_damage + 0.3 * future, gain=gain, deaths=deaths,
                     cap=config.damage_cap, death_penalty=config.death_penalty, tie_weight=tie_weight)
