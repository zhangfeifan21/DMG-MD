#!/usr/bin/env python3
"""Complete-record, one-shot analyzer for the domain NPT registration."""
import argparse
import json
import math
import os
from pathlib import Path
import random
from statistics import NormalDist
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import domain_statistics as ds
import analyze_statistics as stats


def load_records(freeze_path, evidence):
    frozen = json.loads(freeze_path.read_text())
    found = {}
    for path in evidence.glob('*/record.json'):
        record = json.loads(path.read_text())
        key = record.get('job')
        if key in found: raise ValueError(f'duplicate job: {key}')
        if key not in frozen['jobs'] or record.get('metadata') != frozen['jobs'][key]:
            raise ValueError(f'unregistered job: {key}')
        if record.get('status') != 'PASS' or record.get('freeze_sha256') != ds.sha(freeze_path):
            raise ValueError(f'failed job or wrong freeze: {key}')
        if ds.sha(path.parent / 'thermo.out') != record.get('thermo_sha256'):
            raise ValueError(f'thermo changed: {key}')
        inventory = json.loads((Path(frozen['inputs']) / 'inventory.json').read_text())['files']
        job = frozen['jobs'][key]
        expected = {'model.xyz': inventory[job['model']], 'nep.txt': inventory['nep.txt'], 'run.in': inventory[job['run']]}
        if record.get('input_sha256') != expected or any(ds.sha(path.parent / n) != h for n, h in expected.items()):
            raise ValueError(f'inputs changed: {key}')
        for name, digest in record.get('log_sha256', {}).items():
            if ds.sha(path.parent / name) != digest: raise ValueError(f'log changed: {key}')
        if set(record.get('log_sha256', {})) != {'stdout.log', 'stderr.log'}: raise ValueError('missing logs')
        found[key] = path.parent
    if set(found) != set(frozen['jobs']):
        raise ValueError(f'incomplete job matrix: {len(found)}/{len(frozen["jobs"])}')
    return frozen, found


def validate_job_claims(frozen, found, freeze_path):
    ledger = Path(frozen['ledger']); jobs = ledger / 'jobs'
    expected = {key + '.json' for key in frozen['jobs']}
    if {p.name for p in jobs.glob('*.json')} != expected:
        raise ValueError('incomplete or unexpected ledger job claims')
    for key, stage in found.items():
        claim = json.loads((jobs / (key + '.json')).read_text())
        if (claim.get('status') != 'PASS' or claim.get('job') != key
                or claim.get('freeze_sha256') != ds.sha(freeze_path)
                or claim.get('output') != str(stage.resolve())
                or claim.get('record_sha256') != ds.sha(stage / 'record.json')):
            raise ValueError(f'ledger job evidence mismatch: {key}')


def metric_chains(found, profile, condition, seeds):
    values = {metric: {} for metric in ('temperature_K', 'pressure_GPa', 'volume_A3')}
    for seed in seeds:
        key = f"scr-{profile['name']}-p{condition['ranks']}-{condition['backend']}-{condition['mode']}-seed{seed}"
        rows = stats._thermo_rows(found[key] / 'thermo.out', profile['steps'], 5760, profile['dt_fs'])
        for metric in values: values[metric][seed] = rows[metric][profile['burn_steps']:]
    return values


def precision(chains, block, z, minimum_blocks=32):
    if any(len(x) % block or len(x) // block < minimum_blocks for x in chains.values()):
        raise ValueError('invalid complete blocks')
    result = stats._mean_ci(list(chains.values()), block, z)
    center = result['estimate']; reasons = []
    for seed, chain in chains.items():
        if min(chain) == max(chain):
            reasons.append({'seed': seed, 'reason': 'zero variance chain'})
            continue
        squared = [(x - center)**2 for x in chain]
        if min(squared) == max(squared):
            reasons.append({'seed': seed, 'reason': 'constant squared deviation chain'})
    if reasons:
        result['effective_samples_mean'] = 0.0
        result['effective_samples_squared_deviation'] = 0.0
    result['unidentifiable_autocorrelation'] = reasons
    return result


def interval_status(interval, low, high, enough=True):
    if not enough: return 'INCONCLUSIVE'
    if interval[0] >= low and interval[1] <= high: return 'PASS'
    if interval[1] < low or interval[0] > high: return 'FAIL'
    return 'INCONCLUSIVE'


def aggregate(statuses):
    statuses = list(statuses)
    if 'FAIL' in statuses: return 'FAIL'
    if 'INCONCLUSIVE' in statuses: return 'INCONCLUSIVE'
    return 'PASS'


def absolute_mean(chains, block, z, target, bound, reg):
    gates = reg['precision_gates']
    result = precision(chains, block, z, gates['minimum_complete_blocks_per_seed'])
    enough = (result['effective_samples_mean'] >= gates['pooled_ess_per_condition_per_metric'] and
              result['effective_samples_squared_deviation'] >= gates['pooled_squared_deviation_ess_per_condition'])
    status = interval_status(result['confidence_interval'], target - bound, target + bound, enough)
    return {'status': status, 'target': target, 'bound': bound, 'precision': result}


def compare(candidate, reference, block, z, dt, reg):
    bounds = reg['equivalence_bounds']; gates = reg['precision_gates']
    results = {}
    for metric in candidate:
        ca, ref = (precision(candidate[metric], block, z, gates['minimum_complete_blocks_per_seed']),
                   precision(reference[metric], block, z, gates['minimum_complete_blocks_per_seed']))
        enough = all(x['effective_samples_mean'] >= gates['pooled_ess_per_condition_per_metric'] and
                     x['effective_samples_squared_deviation'] >= gates['pooled_squared_deviation_ess_per_condition'] for x in (ca, ref))
        delta = stats._paired_difference_ci(candidate[metric], reference[metric], block, z)
        bound = {'temperature_K': bounds['temperature_mean_K'], 'pressure_GPa': bounds['pressure_mean_GPa'],
                 'volume_A3': bounds['volume_mean_relative'] * ref['estimate']}[metric]
        results[metric] = {'difference': delta, 'candidate_precision': ca, 'reference_precision': ref, 'enough': enough,
                           'bound': bound, 'pass': enough and delta['confidence_interval'][0] >= -bound and
                           delta['confidence_interval'][1] <= bound}
    volume_enough = results['volume_A3']['enough']
    variance = (stats._variance_ratio_ci(candidate['volume_A3'], reference['volume_A3'], block, block * dt, z)
                if volume_enough else {'status': 'INCONCLUSIVE', 'reason': 'insufficient volume precision'})
    cdf = stats._ecdf_distance([x for chain in candidate['volume_A3'].values() for x in chain],
                               [x for chain in reference['volume_A3'].values() for x in chain])
    statuses = [interval_status(x['difference']['confidence_interval'], -x['bound'], x['bound'], x['enough'])
                for x in results.values()]
    statuses.append(interval_status(variance['confidence_interval'], *bounds['variance_ratio'])
                    if volume_enough else 'INCONCLUSIVE')
    statuses.append('PASS' if cdf <= bounds['volume_ecdf_max'] else 'FAIL')
    return {'status': aggregate(statuses), 'metrics': results,
            'variance_ratio': variance, 'volume_ecdf_distance': cdf}


def summaries(chain, block, center=0.0, minimum_blocks=32):
    if len(chain) % block or len(chain) // block < minimum_blocks: raise ValueError('invalid complete block count')
    return [(len(chunk), math.fsum(v-center for v in chunk), math.fsum((v-center)*(v-center) for v in chunk))
            for i in range(0, len(chain), block) for chunk in [chain[i:i+block]]]


def bootstrap_moments(blocks, seeds, rng):
    n = 0; total = 0.0; squares = 0.0
    for _ in seeds:
        seed = rng.choice(seeds)
        options = blocks[seed]
        selected = rng.choices(options, k=len(options))
        n += sum(x[0] for x in selected)
        total += math.fsum(x[1] for x in selected)
        squares += math.fsum(x[2] for x in selected)
    mean = total / n
    variance = max(0.0, (squares - total*total/n) / (n-1))
    return mean, variance


def paired_response_bootstrap(blocks_by_volume, seeds, rng):
    totals = [0.0, 0.0, 0.0]; count = 0
    for _ in seeds:
        seed = rng.choice(seeds)
        count_blocks = len(blocks_by_volume[0][seed])
        if any(len(b[seed]) != count_blocks for b in blocks_by_volume):
            raise ValueError('NVT block alignment mismatch')
        for index in rng.choices(range(count_blocks), k=count_blocks):
            samples = [b[seed][index] for b in blocks_by_volume]
            if any(x[0] != samples[0][0] for x in samples): raise ValueError('NVT block size mismatch')
            count += samples[0][0]
            for i, sample in enumerate(samples): totals[i] += sample[1]
    return [total / count for total in totals]


def response_fit(volumes, pressures):
    xbar, ybar = math.fsum(volumes)/3, math.fsum(pressures)/3
    denom = math.fsum((x-xbar)**2 for x in volumes)
    if denom <= 0: raise ValueError('invalid NVT volume grid')
    slope = math.fsum((x-xbar)*(y-ybar) for x, y in zip(volumes, pressures)) / denom
    if slope >= 0: raise ValueError('nonnegative NVT pressure slope')
    zero = xbar - ybar/slope
    if not min(volumes) <= zero <= max(volumes): raise ValueError('NVT zero pressure outside bracket')
    return zero, -1/(zero*slope)


def prepare_response(found, reg, z):
    nvt = reg['independent_scientific_reference']; nseeds = nvt['seeds']; gates = reg['precision_gates']
    volumes, pressure_blocks, temperatures, points = [], [], {}, {}
    enough = True
    for volume in ('0.99', '1.00', '1.01'):
        pressures = {}; temperature = {}; vs = []
        for seed in nseeds:
            path = found[f'nvt-vol{volume}-seed{seed}'] / 'thermo.out'
            rows = stats._thermo_rows(path, nvt['steps'], 5760, nvt['dt_fs'])
            pressures[seed] = rows['pressure_GPa'][nvt['burn_steps']:]
            temperature[seed] = rows['temperature_K'][nvt['burn_steps']:]
            vs.extend(rows['volume_A3'][nvt['burn_steps']:])
        volumes.append(math.fsum(vs)/len(vs))
        pr = precision(pressures, nvt['block_steps'], z)
        enough = enough and pr['effective_samples_mean'] >= gates['pooled_ess_per_condition_per_metric'] and pr['effective_samples_squared_deviation'] >= gates['pooled_squared_deviation_ess_per_condition']
        pressure_blocks.append({seed: summaries(pressures[seed], nvt['block_steps'], minimum_blocks=gates['minimum_complete_blocks_per_seed']) for seed in nseeds})
        temperatures[volume] = absolute_mean(temperature, nvt['block_steps'], z, reg['temperature_K'],
                                             reg['equivalence_bounds']['temperature_mean_K'], reg)
        points[volume] = {'mean_volume_A3': volumes[-1], 'mean_pressure_GPa': pr['estimate'],
                          'pressure_precision': pr,
                          'complete_blocks_per_seed': {str(seed): len(pressure_blocks[-1][seed]) for seed in nseeds}}
    try:
        v0, kappa = response_fit(volumes, [points[v]['mean_pressure_GPa'] for v in ('0.99', '1.00', '1.01')])
        fit = {'status': 'VALID', 'zero_pressure_volume_A3': v0, 'compressibility_GPa_inverse': kappa}
    except ValueError as error:
        fit = {'status': 'INVALID', 'reason': str(error)}
    diagnostics = {'points': points, 'ols_fit': fit, 'pressure_precision_status': 'PASS' if enough else 'INCONCLUSIVE'}
    return {'volumes': volumes, 'pressure_blocks': pressure_blocks, 'seeds': nseeds, 'enough': enough,
            'temperature_checks': temperatures, 'diagnostics': diagnostics, 'reg': reg}


def scientific_reference(response, candidate, block, z):
    volumes, pressure_blocks, nseeds, enough = (response['volumes'], response['pressure_blocks'],
                                               response['seeds'], response['enough'])
    candidates = candidate['volume_A3']; cseeds = sorted(candidates)
    reg = response['reg']; gates = reg['precision_gates']; bounds = reg['equivalence_bounds']
    if response.get('diagnostics', {}).get('ols_fit', {}).get('status') == 'INVALID':
        return {'status': 'INCONCLUSIVE', 'reason': 'invalid observed NVT response fit'}
    cp = precision(candidates, block, z, gates['minimum_complete_blocks_per_seed'])
    enough = enough and cp['effective_samples_mean'] >= gates['pooled_ess_per_condition_per_metric'] and cp['effective_samples_squared_deviation'] >= gates['pooled_squared_deviation_ess_per_condition']
    center = math.fsum(x for seed in cseeds for x in candidates[seed]) / sum(len(candidates[seed]) for seed in cseeds)
    cb = {seed: summaries(candidates[seed], block, center, gates['minimum_complete_blocks_per_seed']) for seed in cseeds}
    # Volume variance = kBT * kappa[GPa^-1] * V * GPa-per-(eV/A^3).
    replicates = reg['analysis_details']['bootstrap_replicates']
    rng = random.Random(reg['analysis_details']['bootstrap_seed']); diffs = []; ratios = []; curvature = []; invalid = 0
    for _ in range(replicates):
        pressure = paired_response_bootstrap(pressure_blocks, nseeds, rng)
        try:
            v0, kappa = response_fit(volumes, pressure)
        except ValueError:
            invalid += 1; continue
        left = (pressure[1] - pressure[0]) / (volumes[1] - volumes[0])
        right = (pressure[2] - pressure[1]) / (volumes[2] - volumes[1])
        if left >= 0 or right >= 0:
            invalid += 1; continue
        left_root = volumes[1] - pressure[1] / left
        right_root = volumes[1] - pressure[1] / right
        curvature_value = abs(left_root - right_root) / v0
        mean, var = bootstrap_moments(cb, cseeds, rng)
        mean += center
        prediction = (reg['analysis_details']['boltzmann_eV_per_K'] * reg['temperature_K'] *
                      reg['analysis_details']['gpa_per_ev_per_a3'] * kappa * mean)
        if prediction <= 0: invalid += 1; continue
        diffs.append((mean-v0)/v0); ratios.append(var/prediction); curvature.append(curvature_value)
    alpha = math.erfc(z / math.sqrt(2))
    if invalid > math.floor(replicates * alpha / 2):
        return {'status': 'INCONCLUSIVE', 'reason': 'unstable response fit', 'invalid_bootstrap': invalid}
    interval = lambda v: [stats._quantile(v, alpha/2), stats._quantile(v, 1-alpha/2)]
    diff, ratio = interval(diffs), interval(ratios)
    curvature_upper = stats._quantile(curvature, 1-alpha)
    status = aggregate([interval_status(diff, -bounds['volume_mean_relative'], bounds['volume_mean_relative'], enough),
                        interval_status(ratio, *bounds['variance_ratio'], enough),
                        'PASS' if enough and curvature_upper <= bounds['volume_mean_relative'] else 'INCONCLUSIVE'])
    return {'status': status, 'relative_volume_difference_ci': diff,
            'variance_to_response_prediction_ci': ratio, 'curvature_upper': curvature_upper, 'invalid_bootstrap': invalid,
            'candidate_ess': cp, 'replicates': replicates}


def align_half_grid(chain):
    """Thermo samples at dt,2dt,...; post-burn half index 1 matches first base sample."""
    if len(chain) % 2: raise ValueError('half timestep series must contain an even number of samples')
    return chain[1::2]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--freeze', required=True, type=Path); p.add_argument('--evidence', required=True, type=Path)
    p.add_argument('--registration', type=Path, default=HERE / 'domain-statistics-registration.json')
    p.add_argument('--inputs', required=True, type=Path); p.add_argument('--candidate', required=True, type=Path)
    p.add_argument('--environment-proof', required=True, type=Path); p.add_argument('--output', required=True, type=Path)
    p.add_argument('--rng-proof', required=True, type=Path); p.add_argument('--deterministic-proof', required=True, type=Path)
    p.add_argument('--acf-helper', required=True, type=Path)
    a = p.parse_args()
    reg = json.loads(a.registration.read_text())
    ds.validate_freeze(a.freeze, a.registration, a.inputs, a.candidate, a.environment_proof, a.rng_proof, a.deterministic_proof, a.acf_helper)
    ds.validate_gate_proofs(a.rng_proof, a.deterministic_proof, a.candidate, deep=True)
    os.environ['DMGMD_ACF_HELPER'] = str(a.acf_helper.resolve())
    frozen, found = load_records(a.freeze, a.evidence)
    validate_job_claims(frozen, found, a.freeze)
    # Claim the sole final look before loading science data; any failure requires review.
    if a.output.exists(): raise ValueError('analysis output already exists')
    ledger_claim = Path(frozen['ledger']) / 'analysis.json'
    ds.exclusive_json(ledger_claim, {'status': 'RUNNING', 'freeze_sha256': ds.sha(a.freeze),
                                    'evidence': str(a.evidence.resolve()), 'output': str(a.output.resolve())})
    claim = a.evidence / '.domain-npt-analysis-started'
    fd = os.open(claim, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, 'w') as out: json.dump({'freeze_sha256': ds.sha(a.freeze)}, out)
    fd = os.open(a.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, 'w') as out: json.dump({'status': 'ANALYSIS_STARTED', 'freeze_sha256': ds.sha(a.freeze)}, out)
    details = reg['analysis_details']
    stats.BOOTSTRAP_SEED = details['bootstrap_seed']
    stats.BOOTSTRAP_REPLICATES = details['bootstrap_replicates']
    condition_count = sum(len(reg['conditions']) if x['conditions'] == 'all' else len(x['conditions']) for x in reg['profiles'])
    expected_intervals = ((condition_count - len(reg['profiles'])) * 4
                          + len(reg['profiles'][1]['conditions']) * 4 + condition_count * 3
                          + condition_count * 2 + 3)
    if details['interval_count'] != expected_intervals: raise ValueError('registered multiplicity mismatch')
    z = NormalDist().inv_cdf(1 - (1-details['simultaneous_confidence'])/(2 * expected_intervals))
    comparisons = {}
    timestep = {}
    science = {}
    absolute = {}
    response = prepare_response(found, reg, z)
    for profile in reg['profiles']:
        anchor = None
        conditions = reg['conditions'] if profile['conditions'] == 'all' else profile['conditions']
        for condition in conditions:
            current = metric_chains(found, profile, condition, reg['seeds'])
            science_name = f"{profile['name']}-p{condition['ranks']}-{condition['backend']}-{condition['mode']}"
            absolute[science_name] = {
                metric: absolute_mean(current[metric], profile['block_steps'], z, target, bound, reg)
                for metric, target, bound in (
                    ('temperature_K', reg['temperature_K'], reg['equivalence_bounds']['temperature_mean_K']),
                    ('pressure_GPa', 0.0, reg['equivalence_bounds']['pressure_mean_GPa']))}
            if profile['name'] == 'half':
                base_profile = next(x for x in reg['profiles'] if x['name'] == 'base')
                base = metric_chains(found, base_profile, condition, reg['seeds'])
                half_grid = {metric: {seed: align_half_grid(chain) for seed, chain in chains.items()}
                             for metric, chains in current.items()}
                timestep[science_name] = compare(half_grid, base, base_profile['block_steps'], z, base_profile['dt_fs'], reg)
            science[science_name] = scientific_reference(response, current, profile['block_steps'], z)
            if condition['ranks'] == 1:
                anchor = current; continue
            reference = anchor if condition['mode'] == 'off' or profile['name'] == 'half' else metric_chains(found, profile,
                         {'ranks': condition['ranks'], 'backend': condition['backend'], 'mode': 'off'}, reg['seeds'])
            name = f"{profile['name']}-p{condition['ranks']}-{condition['backend']}-{condition['mode']}"
            comparisons[name] = compare(current, reference, profile['block_steps'], z, profile['dt_fs'], reg)
    statuses = ([x['status'] for x in comparisons.values()] + [x['status'] for x in timestep.values()]
                + [x['status'] for x in science.values()]
                + [x['status'] for checks in absolute.values() for x in checks.values()]
                + [x['status'] for x in response['temperature_checks'].values()])
    result = {'status': aggregate(statuses),
              'freeze_sha256': ds.sha(a.freeze), 'parallel_comparisons': comparisons, 'timestep_comparisons': timestep,
              'scientific_reference': science, 'scr_absolute_means': absolute,
              'nvt_absolute_temperature': response['temperature_checks'],
              'nvt_response_diagnostics': response['diagnostics']}
    a.output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    ds.replace_json(ledger_claim, {'status': result['status'], 'freeze_sha256': ds.sha(a.freeze),
                                   'evidence': str(a.evidence.resolve()), 'output': str(a.output.resolve()),
                                   'result_sha256': ds.sha(a.output)})


if __name__ == '__main__': main()
