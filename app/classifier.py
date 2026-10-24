"""One deterministic decision per email, with optional posting-model evidence."""
import html
import json
import math
import re
from pathlib import Path

VERSION = 'rules-1.0'


def tokens(text):
    return re.findall(r'[a-z][a-z]{2,}', html.unescape(re.sub(r'<[^>]+>', ' ', text.lower())))


class PostingModel:
    def __init__(self, path):
        self.data = json.loads(Path(path).read_text(encoding='utf-8')) if Path(path).exists() else None

    def predict(self, text):
        if not self.data:
            return None
        scores = list(self.data['log_prior'])
        for token in tokens(text):
            weights = self.data['weights'].get(token)
            if weights:
                scores = [s + w for s, w in zip(scores, weights)]
        margin = max(-50, min(50, scores[1] - scores[0]))
        return round(1 / (1 + math.exp(-margin)), 4)


def classify(email, model=None):
    # Strip quoted replies so an old offer/interview does not override a new rejection.
    body = re.split(r'(?im)^On .+wrote:|^[- ]*Original Message[- ]*|^From: ', email['body'])[0]
    body = '\n'.join(line for line in body.splitlines() if not line.lstrip().startswith('>'))
    text = email['subject'] + '\n' + body
    lower = text.lower()
    reasons = []
    rules = [
        ('Payment requested to obtain a job or begin training', r'\b(?:pay|send|transfer|deposit|purchase|buy|remit)\b.{0,100}\b(?:fee|deposit|training|equipment|gift card|crypto|bitcoin)\b|\b(?:training|registration|processing|onboarding) fee\b.{0,60}\b(?:required|payable|mandatory|must|pay)\b'),
        ('Sensitive identity or bank information requested by email', r'\b(?:send|share|email|provide|upload|reply with)\b.{0,100}\b(?:passport|aadhaar|aadhar|ssn|social security|bank account|bank details|bank login|otp)\b'),
        ('Check-deposit or money-transfer instructions', r'\b(?:deposit|cash)\b.{0,50}\bcheck\b|\b(?:wire|transfer)\b.{0,60}\b(?:funds|money|remainder)\b'),
        ('Offer promises hiring without an interview', r'\bno interview (?:required|needed|necessary)\b|\bhired without (?:an? )?interview\b'),
    ]
    # Ignore explicitly negated requests sentence-by-sentence.
    sentences = re.split(r'[.!?\n]+', lower)
    for reason, pattern in rules:
        for sentence in sentences:
            if re.search(r'\b(?:never|do not|don\x27t|will not|won\x27t|not required|no fees)\b', sentence):
                continue
            match = re.search(pattern, sentence)
            if match:
                reasons.append({'reason': reason, 'evidence': match.group(0).strip()})
                break
    score = model.predict(text) if model else None
    if reasons:
        if score is not None and score >= .9:
            reasons.append({'reason': 'Job-posting model also found fraud-associated language',
                            'evidence': 'Supporting signal only; this model was evaluated on postings, not emails.'})
        label, explanation = 'scam', 'Potential scam: review the highlighted requests before responding.'
    elif re.search(r'\b(?:unfortunately|not (?:be )?mov(?:ing|e) forward|not selected|unsuccessful|other candidates|position (?:has been |is )?filled|regret to inform|rejected)\b', lower):
        label, explanation = 'rejected', 'The message indicates the application will not proceed.'
    elif re.search(r'\b(?:offer letter|offer of employment|job offer|pleased to offer|extend (?:you )?(?:an? )?offer|offering you|employment offer)\b', lower):
        label, explanation = 'offer', 'The message contains an employment offer.'
    elif re.search(r'\b(?:interview|assessment|screening call|technical round|schedule a call)\b', lower):
        label, explanation = 'interview', 'The message mentions an interview or assessment step.'
    else:
        label, explanation = 'applied', 'Application or acknowledgement recorded; no later-stage signal found.'
    return {'label': label, 'explanation': explanation, 'reasons': reasons,
            'model_score': score, 'classifier_version': VERSION + ('+posting-nb-1' if score is not None else '')}
