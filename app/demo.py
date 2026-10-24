import json
from datetime import datetime, timedelta, timezone


def demo_inbox():
    # Twenty messages, sixteen applications, and four chronological transitions.
    rows = [
        ('northstar', 'Northstar', 'Product Designer', 16, 'Application received', 'Thank you for applying for Product Designer. We have received your application.'),
        ('northstar', 'Northstar', 'Product Designer', 2, 'Meet the design team', 'We loved your portfolio. Please choose a time for your interview with our design lead.'),
        ('orbit', 'Orbit Labs', 'Frontend Engineer', 13, 'Application received', 'Thank you for applying. Our team will review your application shortly.'),
        ('orbit', 'Orbit Labs', 'Frontend Engineer', 1, 'Your employment offer', 'We are pleased to offer you the Frontend Engineer position. The offer letter is attached for review. We never ask applicants to pay training fees.'),
        ('canopy', 'Canopy', 'UX Researcher', 14, 'Application received', 'We have received your application for UX Researcher.'),
        ('canopy', 'Canopy', 'UX Researcher', 4, 'An update on your application', 'Unfortunately, we have decided to move forward with other candidates. Thank you for your time.'),
        ('linear', 'Linear Works', 'Product Designer', 18, 'Application received', 'Thanks for applying for Product Designer. Your application is with our hiring team.'),
        ('linear', 'Linear Works', 'Product Designer', 9, 'Next step: interview', 'We would like to invite you to an interview. Please reply with your availability.'),
        ('figment', 'Figment', 'Design Engineer', 11, 'Thanks for applying', 'Your application is in. Our team will be in touch after reviewing your experience.'),
        ('bloom', 'Bloom Health', 'Frontend Engineer', 8, 'Application confirmation', 'Thank you for applying. We have received your application and will be in touch.'),
        ('loom', 'Loom Studio', 'Product Designer', 3, 'Application received', 'Your application has been received. Thank you for your interest in our team.'),
        ('atlas', 'Atlas', 'UX Engineer', 1, 'Let us meet', 'We would like to schedule a screening call to discuss your experience. Are you free on Tuesday?'),
        ('remote', 'RemoteHub Careers', 'Remote Data Analyst', 2, 'Immediate job offer — action required', 'Congratulations! No interview required. Pay a refundable training fee of INR 4,500 today to secure your role. This offer expires in two hours.'),
        ('vertex', 'Vertex Recruiting', 'Operations Associate', 1, 'Your offer is ready', 'We are pleased to offer you a position. Reply with your passport and bank account details by email to activate your employee profile.'),
        ('apex', 'Apex Talent', 'Virtual Assistant', 5, 'Welcome to the team', 'Your job offer is approved. Deposit the check we send you and transfer the remaining money to our equipment supplier.'),
        ('notion', 'Notion Grove', 'Product Analyst', 6, 'Thank you for your interest', 'Unfortunately, you were not selected for this role. We appreciate the time you invested.'),
        ('pulse', 'Pulse', 'UI Engineer', 10, 'We have your application', 'Your application has been received. We look forward to learning more about you.'),
        ('terra', 'Terra', 'UX Designer', 0, 'Application received', 'Thank you for applying. Your application is now under review.'),
        ('ripple', 'Ripple Systems', 'React Developer', 7, 'Application confirmation', 'We have received your application. The hiring team will review it shortly.'),
        ('mosaic', 'Mosaic', 'Senior Product Designer', 0, 'Technical round invitation', 'We would like to invite you to a technical round with our product team. Please select a time for your interview.'),
    ]
    now = datetime.now(timezone.utc).replace(hour=8, minute=0, second=0, microsecond=0)
    # Stable within a calendar day; the checked-in sample remains stable across imports.
    emails = []
    for i, (key, company, role, days, subject, body) in enumerate(rows):
        emails.append({'application_id': key, 'company': company, 'role': role,
                       'from': f'{company} <careers@{key}.example>', 'date': (now - timedelta(days=days)).isoformat(),
                       'subject': subject, 'body': body, 'message_id': f'<demo-{i}@autopilot.example>'})
    return json.dumps({'emails': emails}).encode()
