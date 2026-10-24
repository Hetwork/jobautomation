"""Train an inspectable, dependency-free posting fraud baseline."""
import csv
import hashlib
import json
import math
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.classifier import tokens


def train(source=ROOT / 'fake_job_postings.csv', output=ROOT / 'data' / 'posting_model.json'):
    csv.field_size_limit(10_000_000)
    groups = [[], []]
    seen = set()
    duplicates = 0
    with open(source, encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            text = ' '.join(row.get(k, '') for k in ('title', 'company_profile', 'description', 'requirements', 'benefits'))
            words = tokens(text)
            fingerprint = hashlib.sha256(' '.join(words).encode()).hexdigest()
            if fingerprint in seen:
                duplicates += 1
                continue
            seen.add(fingerprint)
            groups[int(row['fraudulent'])].append(words)
    rng = random.Random(42)
    training, testing = [[], []], [[], []]
    for label, group in enumerate(groups):
        rng.shuffle(group)
        boundary = int(len(group) * .8)
        training[label], testing[label] = group[:boundary], group[boundary:]
    counts = [Counter(word for document in group for word in document) for group in training]
    vocabulary = {word for word, count in (counts[0] + counts[1]).items() if count >= 3}
    totals = [sum(c[w] for w in vocabulary) + len(vocabulary) for c in counts]
    priors = [math.log(len(group) / sum(map(len, training))) for group in training]
    weights = {word: [math.log((counts[label][word] + 1) / totals[label]) for label in (0, 1)] for word in sorted(vocabulary)}
    tp = fp = tn = fn = 0
    for label, group in enumerate(testing):
        for document in group:
            scores = list(priors)
            for word in document:
                if word in weights:
                    for i in (0, 1):
                        scores[i] += weights[word][i]
            prediction = int(scores[1] > scores[0])
            tp += prediction == 1 and label == 1
            fp += prediction == 1 and label == 0
            tn += prediction == 0 and label == 0
            fn += prediction == 0 and label == 1
    precision, recall = tp / max(1, tp + fp), tp / max(1, tp + fn)
    metrics = {'model': 'Multinomial Naive Bayes', 'dataset': Path(source).name,
               'raw_rows': len(seen) + duplicates, 'unique_rows': len(seen), 'duplicates_removed': duplicates,
               'class_counts': {'real': len(groups[0]), 'fake': len(groups[1])},
               'training_rows': sum(map(len, training)), 'test_rows': sum(map(len, testing)),
               'split': '80/20 stratified holdout after exact-text deduplication; seed 42',
               'vocabulary_size': len(vocabulary), 'precision': round(precision, 4), 'recall': round(recall, 4),
               'f1': round(2 * precision * recall / max(1e-9, precision + recall), 4),
               'accuracy': round((tp + tn) / (tp + tn + fp + fn), 4),
               'confusion_matrix': {'true_positive': tp, 'false_positive': fp, 'true_negative': tn, 'false_negative': fn},
               'limitation': 'Measured on job postings, not emails. Scores are uncalibrated and are only supporting evidence. Near-duplicate postings may remain across the split.'}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'log_prior': priors, 'weights': weights}), encoding='utf-8')
    (output.parent / 'model_metrics.json').write_text(json.dumps(metrics, indent=2), encoding='utf-8')
    print(json.dumps(metrics, indent=2))


if __name__ == '__main__':
    train()
