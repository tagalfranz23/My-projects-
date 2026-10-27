"""Validation and additive updates for confidential resident and family details."""
import hashlib
from datetime import date
from models import db, ResidentFamilyMember, HouseholdMember, Household

DISABILITY_TYPES = ('Visual Disability', 'Hearing Disability', 'Speech Disability',
    'Physical Disability', 'Intellectual Disability', 'Psychosocial Disability',
    'Learning Disability', 'Multiple Disabilities', 'Other', 'Prefer not to specify')


def member_key(name, age, gender):
    return hashlib.sha256(f'{" ".join(name.casefold().split())}|{age}|{gender}'.encode()).hexdigest()


def form_family_rows(form):
    """Redisplay submitted rows after validation, including unsaved entries."""
    names, ages, genders, ids = (form.getlist(key) for key in
        ('family_name', 'family_age', 'family_gender', 'family_id'))
    births, relationships = (form.getlist(key) for key in ('family_date_of_birth', 'family_relationship'))
    return [dict(id=ids[i] if i < len(ids) else '', full_name=name,
                 date_of_birth=births[i] if i < len(births) else '',
                 relationship=relationships[i] if i < len(relationships) else '',
                 age=ages[i] if i < len(ages) else '', gender=genders[i] if i < len(genders) else '')
            for i, name in enumerate(names)]


def validate_family(form, profile=None, registration=False):
    # Old clients that have none of the new fields leave existing information alone.
    if not any(key in form for key in ('family_information', 'is_family_head', 'has_disability',
                                       'disability_type', 'family_name', 'family_id')):
        return None
    head, disability = form.get('is_family_head'), form.get('has_disability')
    if head not in {'Yes', 'No'} or disability not in {'Yes', 'No', 'Prefer not to say'}:
        raise ValueError('Choose a head-of-family answer and a valid disability answer.')
    kind = form.get('disability_type', '').strip() if disability == 'Yes' else None
    if disability == 'Yes' and kind not in DISABILITY_TYPES and bool(kind):
        raise ValueError('Choose a disability type, or Prefer not to specify.')
    other = form.get('disability_other', '').strip() if kind == 'Other' else None
    if other and len(other) > 300:
        raise ValueError('Use at most 300 characters to describe the disability type.')
    rows = []
    if head == 'Yes':
        names = form.getlist('family_name')
        columns = [form.getlist(key) for key in ('family_id','family_name','family_age','family_gender')]
        births, relationships = (form.getlist(key) for key in ('family_date_of_birth', 'family_relationship'))
        if registration:
            columns = [[''] * len(names), names, [''] * len(names), [''] * len(names)]
        if births or relationships or registration:
            if len(births) != len(names) or len(relationships) != len(names):
                raise ValueError('Provide a date of birth and relationship for each family member.')
        if len({len(values) for values in columns}) != 1 or len(columns[0]) > 50:
            raise ValueError('Complete every family member entry (maximum 50 entries).')
        existing = {member.id:member for member in profile.family_members} if profile else {}
        keys, ids = set(), set()
        for index, (raw_id, name, raw_age, gender) in enumerate(zip(*columns)):
            name = ' '.join(name.split())
            birth = births[index] if births else ''
            relationship = relationships[index].strip() if relationships else ''
            if birth or registration:
                try:
                    birth = date.fromisoformat(birth)
                except (TypeError, ValueError):
                    raise ValueError('Enter a valid date of birth for every family member.') from None
                today = date.today()
                age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
                if birth > today or not 0 <= age <= 120 or not relationship or len(relationship) > 80:
                    raise ValueError('Family members need a valid past date of birth and relationship (at most 80 characters).')
                if registration and name.casefold() == ' '.join(form.get('full_name', '').split()).casefold() and birth.isoformat() == form.get('date_of_birth'):
                    raise ValueError('Do not add yourself as your own family member.')
                gender = gender or None
                if gender not in {None, 'Male', 'Female'}:
                    raise ValueError('Invalid family member gender.')
                key = member_key(name, birth.isoformat(), 'birthdate')
            elif raw_age.isdigit() and 0 <= int(raw_age) <= 120 and gender in {'Male','Female'}:
                age, birth = int(raw_age), None
                key = member_key(name, age, gender)
            else:
                raise ValueError('Each family member needs a full name, age from 0 to 120, and Male or Female gender.')
            if not name or len(name) > 150:
                raise ValueError('Family member names are required and must be at most 150 characters.')
            record_id = None
            if raw_id:
                if not raw_id.isdigit() or int(raw_id) not in existing or int(raw_id) in ids:
                    raise ValueError('The family member does not belong to this resident, or was submitted twice.')
                record_id = int(raw_id); ids.add(record_id)
            if key in keys:
                raise ValueError('The same family member was entered more than once. Remove the duplicate entry.')
            keys.add(key)
            rows.append(dict(id=record_id, full_name=name, age=age, gender=gender, member_key=key,
                             date_of_birth=birth, relationship=relationship or None))
        retained_keys = {member.member_key for member in existing.values() if member.id not in ids}
        for row in rows:
            if row['id'] and row['member_key'] in retained_keys:
                raise ValueError('These details duplicate an existing family member.')
    return dict(head=head == 'Yes', has_disability=None if disability == 'Prefer not to say' else disability == 'Yes',
                disability_declined=disability == 'Prefer not to say',
                disability_type=kind or None, disability_other=other or None, members=rows)


def save_family(profile, data, actor_id):
    if data is None:
        return
    profile.is_household_head = data['head']
    profile.has_disability = data['has_disability']
    profile.disability_declined = data['disability_declined']
    profile.disability_type = data['disability_type']
    profile.disability_other = data['disability_other']
    existing = {member.id:member for member in profile.family_members}
    # Reuse an unambiguous existing household; never move or create memberships here.
    memberships = HouseholdMember.query.filter_by(resident_id=profile.user_id, membership_status='Active').all()
    household_ids = {item.household_id for item in memberships}
    household_ids.update(row.id for row in Household.query.filter_by(head_resident_id=profile.user_id).all())
    household_id = next(iter(household_ids)) if len(household_ids) == 1 else None
    updated_ids = {row['id'] for row in data['members'] if row['id']}
    keys = {member.member_key:member for member in existing.values() if member.id not in updated_ids}
    for row in sorted(data['members'], key=lambda item: item['id'] is None):
        item = existing.get(row['id']) if row['id'] else keys.get(row['member_key'])
        if item is None:
            item = ResidentFamilyMember(head_profile_id=profile.id, household_id=household_id,
                                        created_by=actor_id, updated_by=actor_id)
            db.session.add(item)
        # Preserve birthdate/relationship on legacy age/gender submissions.
        for key in ('full_name','age','gender','member_key'):
            setattr(item, key, row[key])
        if row.get('date_of_birth'):
            item.date_of_birth = row['date_of_birth']
            item.relationship = row['relationship']
        item.updated_by = actor_id
        keys[item.member_key] = item
    # Missing rows and switching to No deliberately do not delete saved members.
