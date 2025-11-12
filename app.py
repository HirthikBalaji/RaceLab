import logging
import json
import os
import datetime
import csv
import io
import re
from flask import send_file, Flask, request, render_template, redirect, url_for, session, flash, Response
from functools import wraps
# ... in main.py, at the top ...
from flask import send_file, Flask, request, render_template, redirect, url_for, session, flash, Response, abort,send_from_directory
# ...
# --- START: Re-added itsdangerous for secure links ---
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature

# --- END: Re-added ---

# --- Configuration ---
app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

# --- START: Initialize Serializer ---
s = URLSafeTimedSerializer(app.secret_key)
# --- END: Initialize Serializer ---


# --- Constants ---
COMPONENTS_DB = 'components.json'
REQUESTS_DB = 'requests.json'
USERS_DB = 'users.json'

# --- Logging (Unchanged) ---
logging.basicConfig(
    filename='activity.log', level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
audit_handler = logging.FileHandler('audit.log')
audit_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
audit_logger = logging.getLogger('audit_logger')
audit_logger.setLevel(logging.INFO)
audit_logger.addHandler(audit_handler)


# --- JSON Helper Functions (CORRECTED) ---

def load_components():
    """Loads components.json safely."""
    if not os.path.exists(COMPONENTS_DB) or os.path.getsize(COMPONENTS_DB) == 0:
        return []
    try:
        with open(COMPONENTS_DB, 'r') as f:
            return json.load(f)
    except json.JSONDecodeError:
        app.logger.error(f"Failed to decode {COMPONENTS_DB}. Returning empty list.")
        return []


def save_components(data):
    with open(COMPONENTS_DB, 'w') as f: json.dump(data, f, indent=4)


def load_requests():
    """Loads requests.json safely."""
    if not os.path.exists(REQUESTS_DB) or os.path.getsize(REQUESTS_DB) == 0: return []
    try:
        with open(REQUESTS_DB, 'r') as f:
            return json.load(f)
    except json.JSONDecodeError:
        app.logger.error(f"Failed to decode {REQUESTS_DB}. Returning empty list.")
        return []


def save_requests(data):
    with open(REQUESTS_DB, 'w') as f: json.dump(data, f, indent=4)


def load_staff_users():
    """Loads users.json safely."""
    if not os.path.exists(USERS_DB) or os.path.getsize(USERS_DB) == 0:
        return []
    try:
        with open(USERS_DB, 'r') as f:
            return json.load(f)
    except json.JSONDecodeError:
        app.logger.error(f"Failed to decode {USERS_DB}. Returning empty list.")
        return []


def get_staff_by_email(email):
    for user in load_staff_users():
        if user['email'] == email:
            return user
    return None


# --- CORRECTED AUGMENTED COMPONENTS FUNCTION ---
def get_augmented_components():
    """
    Loads components from JSON and calculates the 'available' count.
    It trusts the 'working_quantity' and 'issued_quantity'
    fields which are correctly managed by the technician routes.
    """
    components = load_components()
    augmented_components = []

    for comp in components:
        # Get quantities, using .get() with a default of 0 for safety
        working_qty = comp.get('working_quantity', 0)
        issued_qty = comp.get('issued_quantity', 0)

        # Set default values for any missing keys in the component object
        # This prevents errors in the templates if a component is missing data
        comp.setdefault('name', 'Unknown Component')
        comp.setdefault('total_quantity', 0)
        comp.setdefault('working_quantity', working_qty)
        comp.setdefault('not_working_quantity', 0)
        comp.setdefault('issued_quantity', issued_qty)

        # Calculate the 'available' count
        comp['available'] = working_qty - issued_qty

        # Ensure 'available' is not negative
        if comp['available'] < 0:
            comp['available'] = 0
            app.logger.warning(
                f"Component {comp['name']} has negative availability. {working_qty} working, {issued_qty} issued.")

        augmented_components.append(comp)

    return augmented_components


# --- Email Parsing Logic (Unchanged) ---
CAMPUS_MAP = {'ch': 'Chennai'}
SCHOOL_MAP = {'en': 'School of Engineering', 'sc': 'School of Computing'}
DEPT_MAP = {
    'rai': 'Robotics & AI', 'cse': 'Computer Science', 'ece': 'Electronics & Comm.'
}
YEAR_MAP = {
    '25': '1st Year', '24': '2nd Year', '23': '3rd Year', '22': '4th Year'
}
STUDENT_DOMAIN = 'ch.students.amrita.edu'
FACULTY_DOMAIN = 'ch.amrita.edu'


def parse_student_email(email):
    try:
        username, domain = email.split('@')
        if domain != STUDENT_DOMAIN: return None
        parts = username.split('.')
        if len(parts) != 3: return None
        campus_code, school_code, id_part = parts[0], parts[1], parts[2]
        dept_code = id_part[2:5]
        roll_number = id_part[5:]
        year_code = roll_number[:2]
        user_data = {
            'email': email, 'role': 'student',
            'name': f"Student {roll_number}",
            'campus': CAMPUS_MAP.get(campus_code, campus_code.upper()),
            'school': SCHOOL_MAP.get(school_code, school_code.upper()),
            'department': DEPT_MAP.get(dept_code, dept_code.upper()),
            'year': YEAR_MAP.get(year_code, f"Year {year_code}"),
            'roll_number': roll_number
        }
        return user_data
    except Exception as e:
        app.logger.error(f"Failed to parse email {email}: {e}")
        return None


def parse_faculty_email(email):
    try:
        username, domain = email.split('@')
        if domain != FACULTY_DOMAIN:
            return None

        name_parts = username.split('.')
        name = ' '.join([part.capitalize() for part in name_parts])

        user_data = {
            'email': email,
            'role': 'faculty',
            'name': f"Faculty {name}",
            'department': "Faculty",
            'year': None
        }
        return user_data
    except Exception as e:
        app.logger.error(f"Failed to parse faculty email {email}: {e}")
        return None


# --- Routes ---

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(
        os.path.join(app.root_path, 'static', 'icons'),
        'favicon.ico',
        mimetype='image/vnd.microsoft.icon'
    )

@app.route('/')
def home():
    error = session.pop('error', None)
    return render_template('index.html', error=error)


@app.route('/login', methods=['POST'])
def login():
    email = request.form.get('email')
    password = request.form.get('password')

    staff_user = get_staff_by_email(email)
    if staff_user:
        if staff_user['password'] == password:
            app.logger.info(f'LOGIN SUCCESS (Staff): User "{email}" ({staff_user["role"]}) logged in.')
            session['user'] = staff_user

            if staff_user['role'] == 'admin':
                return redirect(url_for('admin_dashboard'))
            elif staff_user['role'] == 'hod':
                return redirect(url_for('hod_dashboard'))
            elif staff_user['role'] == 'faculty':
                return redirect(url_for('faculty_dashboard'))
            elif staff_user['role'] == 'technician':
                return redirect(url_for('tech_dashboard'))
            else:
                return redirect(url_for('home'))
        else:
            app.logger.warning(f'LOGIN FAILED (Staff): Bad password for user "{email}"')
            session['error'] = 'Invalid email or password.'
            return redirect(url_for('home'))

    faculty_user = parse_faculty_email(email)
    if faculty_user:
        app.logger.info(f'LOGIN SUCCESS (Faculty Pattern): Parsed user "{email}"')
        session['user'] = faculty_user
        return redirect(url_for('faculty_dashboard'))

    student_user = parse_student_email(email)
    if student_user:
        app.logger.info(f'LOGIN SUCCESS (Student): Parsed user "{email}"')
        session['user'] = student_user
        return redirect(url_for('student_dashboard'))

    app.logger.warning(f'LOGIN FAILED: Unknown user or invalid email format "{email}"')
    session['error'] = 'Invalid email or password.'
    return redirect(url_for('home'))


@app.route('/logout')
def logout():
    email = session.pop('user', {'email': 'Unknown'})['email']
    app.logger.info(f'LOGOUT: User "{email}" logged out.')
    return redirect(url_for('home'))


# --- Security Wrapper (Unchanged) ---
def role_required(roles):
    if isinstance(roles, str):
        roles = [roles]

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user' not in session or session['user']['role'] not in roles:
                flash('You do not have permission to access this page.', 'error')
                return redirect(url_for('home'))
            return f(*args, **kwargs)

        return decorated_function

    return decorator


# --- Student Routes ---
@app.route('/student_dashboard')
@role_required('student')
def student_dashboard():
    user = session['user']
    all_components = get_augmented_components()
    all_requests = load_requests()
    my_requests = [req for req in all_requests if req['student_email'] == user['email']]
    today = datetime.date.today().strftime("%Y-%m-%d")
    max_date = (datetime.date.today() + datetime.timedelta(days=30)).strftime("%Y-%m-%d")

    available_components_for_form = [c for c in all_components if c['available'] > 0]

    return render_template('student_dashboard.html',
                           user=user,
                           components=all_components,
                           available_components=available_components_for_form,
                           my_requests=my_requests,
                           today=today,
                           max_date=max_date)


@app.route('/cancel_request/<int:request_id>', methods=['POST'])
def cancel_request(request_id):
    if 'user' not in session:
        flash('You must be logged in to perform this action.', 'error')
        return redirect(url_for('home'))

    user_email = session['user']['email']
    user_role = session['user']['role']

    all_requests = load_requests()
    target_request = next((req for req in all_requests if req['id'] == request_id), None)

    if not target_request:
        flash('Request not found.', 'error')
        return redirect(request.referrer or url_for('home'))

    cancel_remarks = request.form.get('cancel_remarks', '').strip()
    if not cancel_remarks:
        flash('Cancellation remarks are mandatory.', 'error')
        return redirect(request.referrer or url_for('home'))

    is_owner = target_request['student_email'] == user_email
    is_technician = user_role == 'technician'

    if not is_owner and not is_technician:
        flash('You are not authorized to cancel this request.', 'error')
        return redirect(request.referrer or url_for('home'))

    cancellable_statuses = []
    if is_owner:
        cancellable_statuses.extend(['Pending Mentor', 'Pending Incharge', 'Approved', 'Pending Purchase'])
    if is_technician:
        cancellable_statuses.append('Approved')

    if target_request['status'] in set(cancellable_statuses):
        original_status = target_request['status']
        target_request['status'] = 'Cancelled'
        cancellation_remark = f"Cancelled by {user_role} ({user_email}): {cancel_remarks}"

        # Store remark in a logical place
        if original_status == 'Approved' and is_technician:
            target_request['tech_remarks'] = cancellation_remark
        else:
            target_request['incharge_remarks'] = cancellation_remark

        save_requests(all_requests)
        audit_logger.info(
            f'CANCELLED by {user_email}: Req #{request_id} (was {original_status}). Remarks: {cancel_remarks}')
        flash(f'Request #{request_id} has been successfully cancelled.', 'success')
    else:
        flash(f'Request #{request_id} cannot be cancelled (Status: {target_request["status"]}).', 'error')

    # Redirect back to the dashboard they came from
    if user_role == 'student':
        return redirect(url_for('student_dashboard'))
    elif user_role == 'faculty':
        return redirect(url_for('faculty_dashboard'))
    elif user_role == 'technician':
        return redirect(url_for('tech_dashboard'))
    else:
        return redirect(url_for('home'))


# --- START: HEAVILY MODIFIED REQUEST ROUTE (Handles Project Types) ---
@app.route('/request_component', methods=['POST'])
@role_required('student')
def request_component():
    user = session['user']
    all_requests = load_requests()
    all_components = get_augmented_components()

    # --- NEW: Get the project type from the hidden input ---
    project_type = request.form.get('project_type')
    if not project_type:
        flash('Error: Invalid request type. Please select a project type.', 'error')
        return redirect(url_for('student_dashboard'))

    mentor_name = request.form.get('mentor_name')
    mentor_email = request.form.get('mentor_email')
    project_description = request.form.get('project_description')
    return_date_str = request.form.get('return_date')
    component_names = request.form.getlist('component[]')
    quantities = request.form.getlist('quantity[]')

    if not component_names or not quantities or len(component_names) != len(quantities):
        flash('Error: Mismatched component or quantity data. Please try again.', 'error')
        return redirect(url_for('student_dashboard'))

    request_date_dt = datetime.datetime.now()
    return_date_dt = datetime.datetime.strptime(return_date_str, '%Y-%m-%d')
    duration = (return_date_dt.date() - request_date_dt.date()).days + 1
    approval_time = request_date_dt.strftime("%Y-%m-%d %H:%M")

    # --- Date validation based on project type ---
    if project_type == 'Intra-Day':
        if return_date_dt.date() != request_date_dt.date():
            flash(f"Error: Intra-Day requests must be returned on the same day.", 'error')
            return redirect(url_for('student_dashboard'))
    else:  # Project Work & Competition
        if duration > 30:
            flash(f"Error: Return date is more than 30 days away. Max is 30 days.", 'error')
            return redirect(url_for('student_dashboard'))
        if duration < 1:
            flash(f"Error: The return date must be today or in the future.", 'error')
            return redirect(url_for('student_dashboard'))

    # --- START: Pre-flight Validation (Same as before) ---
    batch_requirements = {}
    for i in range(len(component_names)):
        comp_name = component_names[i].strip()
        try:
            quantity = int(quantities[i])
            if quantity <= 0:
                flash(f"Error: Invalid quantity for '{comp_name}'. Must be 1 or more.", 'error')
                return redirect(url_for('student_dashboard'))
            batch_requirements[comp_name] = batch_requirements.get(comp_name, 0) + quantity
        except ValueError:
            flash(f"Error: Invalid quantity for '{comp_name}'.", 'error')
            return redirect(url_for('student_dashboard'))

    if not batch_requirements:
        flash('Error: No valid components submitted.', 'error')
        return redirect(url_for('student_dashboard'))

    for comp_name, total_needed in batch_requirements.items():
        component_obj = next((c for c in all_components if c['name'] == comp_name), None)
        if not component_obj:
            flash(f"Error: Component '{comp_name}' does not exist.", 'error')
            return redirect(url_for('student_dashboard'))
        if total_needed > component_obj['available']:
            flash(
                f"Error: Insufficient stock for '{comp_name}'. Requested {total_needed}, only {component_obj['available']} available.",
                'error')
            return redirect(url_for('student_dashboard'))
    # --- END: Pre-flight Validation ---

    # --- START: Workflow Logic based on Project Type ---
    batch_id = f"B-{request_date_dt.strftime('%Y%m%d%H%M%S')}"
    log_message = ""

    # Variables for the new_request object
    new_status = ""
    req_mentor_name = ""
    req_mentor_email = ""
    mentor_app_time = None
    hod_app_time = None
    incharge_app_email = None
    incharge_app_time = None
    mentor_remarks = None
    incharge_remarks = None
    batch_token = None

    if project_type == 'Intra-Day':
        # Auto-approve and send straight to technician
        new_status = "Approved"
        req_mentor_name = "N/A (Intra-Day)"
        req_mentor_email = "N/A"
        mentor_app_time = approval_time
        hod_app_time = approval_time  # Bypass HOD
        incharge_app_email = "System (Intra-Day)"
        incharge_app_time = approval_time  # Bypass Incharge approval
        mentor_remarks = "Intra-Day Activity"
        incharge_remarks = "Auto-approved for Technician Issue."
        batch_token = None  # No mentor link needed
        log_message = f'INTRA-DAY BATCH SUBMITTED: User "{user["email"]}" batch {batch_id}. Auto-approved for Technician.'

    elif project_type == 'Project Work':
        # NEW: Project Work flow (Bypass Mentor, goes to Incharge)
        new_status = "Pending Incharge"
        req_mentor_name = "N/A (Project Work)"
        req_mentor_email = "N/A"
        mentor_app_time = approval_time  # Auto-bypass mentor
        hod_app_time = approval_time  # Auto-bypass HOD
        incharge_app_email = None  # Needs Incharge approval
        incharge_app_time = None  # Needs Incharge approval
        mentor_remarks = "Project Work (Mentor Bypassed)"
        incharge_remarks = None
        batch_token = None  # No mentor link needed
        log_message = f'PROJECT WORK BATCH SUBMITTED: User "{user["email"]}" batch {batch_id}. Awaiting Incharge.'

    else:  # Competition (or any other type)
        # Standard Mentor -> Incharge workflow
        new_status = "Pending Mentor"
        req_mentor_name = mentor_name
        req_mentor_email = mentor_email
        mentor_app_time = None
        hod_app_time = None
        incharge_app_email = None
        incharge_app_time = None
        mentor_remarks = None
        incharge_remarks = None
        batch_token = s.dumps(batch_id)  # Needs mentor approval
        log_message = f'REQUEST BATCH SUBMITTED: User "{user["email"]}" batch {batch_id} ({project_type}). Awaiting Mentor.'

    # --- END: Workflow Logic ---

    current_request_id = (max(req['id'] for req in all_requests) if all_requests else 0) + 1
    new_requests_list = []

    for comp_name, quantity in batch_requirements.items():
        component_obj = next((c for c in all_components if c['name'] == comp_name), None)

        new_request = {
            "id": current_request_id,
            "batch_id": batch_id,
            "request_type": "borrow",
            "project_type": project_type,  # --- NEW FIELD ---
            "status": new_status,
            "request_timestamp": request_date_dt.strftime("%Y-%m-%d %H:%M"),
            "hod_remarks": None,
            "incharge_remarks": incharge_remarks,
            "student_email": user['email'],
            "student_name": user['name'],
            "student_dept": user['department'],
            "student_year": user['year'],
            "component_id": component_obj['id'],
            "component_name": component_obj['name'],
            "quantity": quantity,
            "project_description": project_description,
            "due_date": return_date_str,
            "duration_days": duration,
            "mentor_name": req_mentor_name,
            "mentor_email": req_mentor_email,
            "mentor_approval_token": batch_token,
            "mentor_remarks": mentor_remarks,
            "mentor_approval_timestamp": mentor_app_time,
            "hod_approval_timestamp": hod_app_time,
            "approver_email": incharge_app_email,
            "approval_timestamp": incharge_app_time,
            "issue_timestamp": None,
            "actual_return_timestamp": None,
            "working_count": None,
            "not_working_count": None,
            "tech_remarks": None,
            "purchase_link": None
        }
        new_requests_list.append(new_request)
        current_request_id += 1

    all_requests.extend(new_requests_list)
    save_requests(all_requests)

    app.logger.info(log_message)
    flash(f'{len(new_requests_list)} component request(s) for {project_type} have been submitted.', 'success')
    return redirect(url_for('student_dashboard'))


# --- END: MODIFIED /request_component ---


@app.route('/approve/mentor/<token>', methods=['GET', 'POST'])
def mentor_approval(token):
    try:
        batch_id = s.loads(token, max_age=259200)  # 72 hours
    except SignatureExpired:
        return render_template('mentor_response.html', title="Expired",
                               message="This approval link has expired (older than 72 hours). Please ask the student to resubmit their request."), 400
    except BadTimeSignature:
        return render_template('mentor_response.html', title="Invalid Link",
                               message="This approval link is invalid or has already been used."), 400

    all_requests = load_requests()
    batch_requests = [req for req in all_requests if
                      req.get('batch_id') == batch_id and req['status'] == 'Pending Mentor']

    if not batch_requests:
        already_processed = any(req.get('batch_id') == batch_id for req in all_requests)
        if already_processed:
            return render_template('mentor_response.html', title="Already Processed",
                                   message="This request batch has already been processed (approved, rejected, or updated)."), 400
        else:
            return render_template('mentor_response.html', title="Not Found",
                                   message="This request batch could not be found."), 404

    if request.method == 'POST':
        new_status = request.form.get('new_status')
        mentor_remarks = request.form.get('mentor_remarks', '').strip()
        approval_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

        for req in batch_requests:
            req['mentor_approval_timestamp'] = approval_time
            req['mentor_approval_token'] = None
            req['mentor_remarks'] = mentor_remarks if mentor_remarks else None

            if new_status == 'Approved':
                req['status'] = 'Pending Incharge'  # Skip HOD
            elif new_status == 'Rejected':
                req['status'] = 'Rejected'

        save_requests(all_requests)

        if new_status == 'Approved':
            return render_template('mentor_response.html', title="Approved",
                                   message="Thank you. The request batch has been approved and forwarded to the Lab Incharge.")
        else:
            return render_template('mentor_response.html', title="Rejected",
                                   message="The request batch has been marked as rejected.")

    # If GET request, show the approval form
    return render_template('mentor_approval.html',
                           batch_requests=batch_requests,
                           shared_request=batch_requests[0],
                           token=token)


# --- Faculty Routes (Unchanged) ---
@app.route('/faculty_dashboard')
@role_required('faculty')
def faculty_dashboard():
    user = session['user']
    all_components = get_augmented_components()
    all_requests = load_requests()

    my_requests = [req for req in all_requests if req['student_email'] == user['email']]
    my_requests.sort(key=lambda x: x['request_timestamp'], reverse=True)

    available_components_for_form = [c for c in all_components if c['available'] > 0]
    today = datetime.date.today().strftime("%Y-%m-%d")

    return render_template('faculty_dashboard.html',
                           user=user,
                           components=all_components,
                           available_components=available_components_for_form,
                           my_requests=my_requests,
                           today=today)


@app.route('/faculty_request', methods=['POST'])
@role_required('faculty')
def faculty_request():
    user = session['user']
    all_requests = load_requests()
    all_components = get_augmented_components()
    request_type = request.form.get('request_type')

    batch_id = f"B-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
    request_date = datetime.datetime.now()
    current_request_id = (max(req['id'] for req in all_requests) if all_requests else 0) + 1
    approval_time = request_date.strftime("%Y-%m-%d %H:%M")

    if request_type == 'borrow':
        project_description = request.form.get('project_description')
        return_date_str = request.form.get('return_date')
        component_names = request.form.getlist('component[]')
        quantities = request.form.getlist('quantity[]')

        if not component_names or not quantities or len(component_names) != len(quantities):
            flash('Error: Mismatched component or quantity data.', 'error')
            return redirect(url_for('faculty_dashboard'))

        return_date_dt = datetime.datetime.strptime(return_date_str, '%Y-%m-%d')
        duration = (return_date_dt.date() - request_date.date()).days + 1

        if duration < 1:
            flash(f"Error: The return date must be today or in the future.", 'error')
            return redirect(url_for('faculty_dashboard'))

        batch_requirements = {}
        for i in range(len(component_names)):
            comp_name = component_names[i].strip()
            try:
                quantity = int(quantities[i])
                if quantity <= 0:
                    flash(f"Error: Invalid quantity for '{comp_name}'.", 'error')
                    return redirect(url_for('faculty_dashboard'))
                batch_requirements[comp_name] = batch_requirements.get(comp_name, 0) + quantity
            except ValueError:
                flash(f"Error: Invalid quantity for '{comp_name}'.", 'error')
                return redirect(url_for('faculty_dashboard'))

        if not batch_requirements:
            flash('Error: No valid components submitted.', 'error')
            return redirect(url_for('faculty_dashboard'))

        for comp_name, total_needed in batch_requirements.items():
            component_obj = next((c for c in all_components if c['name'] == comp_name), None)
            if not component_obj:
                flash(f"Error: Item '{comp_name}' does not exist.", 'error')
                return redirect(url_for('faculty_dashboard'))
            if total_needed > component_obj['available']:
                flash(
                    f"Error: Insufficient stock for '{comp_name}'. Requested {total_needed}, available {component_obj['available']}.",
                    'error')
                return redirect(url_for('faculty_dashboard'))

        new_requests_list = []
        for comp_name, quantity in batch_requirements.items():
            component_obj = next((c for c in all_components if c['name'] == comp_name), None)
            new_request = {
                "id": current_request_id,
                "batch_id": batch_id,
                "request_type": "borrow",
                "project_type": "Faculty Project",  # --- NEW FIELD ---
                "status": "Pending Incharge",
                "request_timestamp": request_date.strftime("%Y-%m-%d %H:%M"),
                "hod_remarks": None,
                "incharge_remarks": None,
                "student_email": user['email'],
                "student_name": user['name'],
                "student_dept": user['department'],
                "student_year": None,
                "component_id": component_obj['id'],
                "component_name": component_obj['name'],
                "quantity": quantity,
                "project_description": project_description,
                "due_date": return_date_str,
                "duration_days": duration,
                "mentor_name": "Faculty (Self-Approved)",
                "mentor_email": user['email'],
                "mentor_approval_token": None,
                "mentor_remarks": "Faculty Request",
                "mentor_approval_timestamp": approval_time,
                "hod_approval_timestamp": approval_time,
                "approver_email": None,
                "approval_timestamp": None,
                "issue_timestamp": None,
                "actual_return_timestamp": None,
                "working_count": None,
                "not_working_count": None,
                "tech_remarks": None,
                "purchase_link": None
            }
            new_requests_list.append(new_request)
            current_request_id += 1

        all_requests.extend(new_requests_list)
        save_requests(all_requests)
        app.logger.info(
            f'FACULTY BORROW REQUEST: User "{user["email"]}" submitted batch {batch_id}. Bypassed to Incharge.')
        flash(f'{len(new_requests_list)} component request(s) submitted and sent to Lab Incharge.', 'success')

    elif request_type == 'purchase':
        purchase_comp_name = request.form.get('purchase_component_name').strip()
        purchase_quantity = int(request.form.get('purchase_quantity'))
        purchase_project = request.form.get('purchase_project').strip()
        purchase_link = request.form.get('purchase_link', '').strip()

        if not purchase_comp_name or purchase_quantity <= 0 or not purchase_project:
            flash('Error: All fields are required for a purchase request.', 'error')
            return redirect(url_for('faculty_dashboard'))

        new_purchase_request = {
            "id": current_request_id,
            "batch_id": batch_id,
            "request_type": "purchase",
            "project_type": "Faculty Purchase",  # --- NEW FIELD ---
            "status": "Pending Incharge",
            "request_timestamp": request_date.strftime("%Y-%m-%d %H:%M"),
            "hod_remarks": None,
            "incharge_remarks": None,
            "student_email": user['email'],
            "student_name": user['name'],
            "student_dept": user['department'],
            "student_year": None,
            "component_id": "PURCHASE",
            "component_name": purchase_comp_name,
            "quantity": purchase_quantity,
            "project_description": purchase_project,
            "due_date": None,
            "duration_days": None,
            "mentor_name": "Faculty (Self-Approved)",
            "mentor_email": user['email'],
            "mentor_approval_token": None,
            "mentor_remarks": "Faculty Purchase Request",
            "mentor_approval_timestamp": approval_time,
            "hod_approval_timestamp": approval_time,
            "approver_email": None,
            "approval_timestamp": None,
            "issue_timestamp": None,
            "actual_return_timestamp": None,
            "working_count": None,
            "not_working_count": None,
            "tech_remarks": None,
            "purchase_link": purchase_link if purchase_link else None
        }

        all_requests.append(new_purchase_request)
        save_requests(all_requests)
        app.logger.info(
            f'FACULTY PURCHASE REQUEST: User "{user["email"]}" submitted batch {batch_id} for "{purchase_comp_name}". Awaiting Incharge.')
        flash('New component purchase request submitted and sent to Incharge for approval.', 'success')

    return redirect(url_for('faculty_dashboard'))


# --- HOD Routes (Unchanged) ---
@app.route('/hod_dashboard')
@role_required('hod')
def hod_dashboard():
    all_requests = load_requests()
    components = get_augmented_components()
    other_requests = sorted(all_requests, key=lambda x: x['request_timestamp'], reverse=True)

    return render_template('hod_dashboard.html',
                           user=session['user'],
                           other_requests=other_requests,
                           components=components)


# --- Admin (Incharge) Routes ---
@app.route('/admin')
@role_required('admin')
def admin_dashboard():
    all_requests = load_requests()
    components = get_augmented_components()

    pending_incharge_borrow = []
    pending_incharge_purchases = []
    other_requests = []

    for req in all_requests:
        if req['status'] == 'Pending Incharge':
            if req.get('request_type') == 'purchase':
                pending_incharge_purchases.append(req)
            else:
                pending_incharge_borrow.append(req)
        else:
            other_requests.append(req)

    grouped_pending_borrow = {}
    for req in pending_incharge_borrow:
        batch_id = req.get('batch_id', f"req-{req['id']}")
        if batch_id not in grouped_pending_borrow:
            grouped_pending_borrow[batch_id] = []
        grouped_pending_borrow[batch_id].append(req)

    other_requests.sort(key=lambda x: x['request_timestamp'], reverse=True)
    pending_incharge_purchases.sort(key=lambda x: x['request_timestamp'], reverse=True)

    return render_template('admin_dashboard.html',
                           user=session['user'],
                           grouped_pending_requests=grouped_pending_borrow,
                           pending_purchases=pending_incharge_purchases,
                           other_requests=other_requests,
                           components=components)


@app.route('/admin/update_request', methods=['POST'])
@role_required('admin')
def admin_update_request():
    batch_id = request.form.get('batch_id')
    new_status = request.form.get('new_status')  # "Approved" or "Rejected"
    admin_user = session['user']
    incharge_remarks = request.form.get('incharge_remarks', '').strip()

    all_requests = load_requests()
    all_components = get_augmented_components()

    if not batch_id:
        flash('Error: No Batch ID provided.', 'error')
        return redirect(url_for('admin_dashboard'))

    if batch_id.startswith('req-'):
        req_id = int(batch_id.split('-')[1])
        batch_requests = [req for req in all_requests if req['id'] == req_id and req['status'] == 'Pending Incharge']
    else:
        batch_requests = [req for req in all_requests if
                          req.get('batch_id') == batch_id and req['status'] == 'Pending Incharge']

    if not batch_requests:
        flash('Error: Request batch not found or already processed.', 'error')
        return redirect(url_for('admin_dashboard'))

    approval_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    approved_count = 0
    rejected_count = 0

    for req in batch_requests:
        req['approval_timestamp'] = approval_time
        req['approver_email'] = admin_user['email']

        if new_status == 'Approved':
            target_component = next((comp for comp in all_components if comp['name'] == req['component_name']), None)
            if target_component:
                available_stock = target_component['available']
                if available_stock >= req['quantity']:
                    req['status'] = 'Approved'
                    req['incharge_remarks'] = incharge_remarks if incharge_remarks else "Approved."
                    approved_count += 1
                    audit_logger.info(
                        f'APPROVAL by {admin_user["email"]}: Req #{req["id"]}. Item {req["component_name"]} approved for issue.')
                else:
                    req['status'] = 'Rejected'
                    rejection_note = f"Auto-rejected: Insufficient stock (Only {available_stock} available.)"
                    req['incharge_remarks'] = f"{rejection_note} {incharge_remarks}".strip()
                    rejected_count += 1
            else:
                req['status'] = 'Rejected'
                req['incharge_remarks'] = f"Auto-rejected: Component not found in database. {incharge_remarks}".strip()
                rejected_count += 1

        elif new_status == 'Rejected':
            req['status'] = 'Rejected'
            req['incharge_remarks'] = incharge_remarks if incharge_remarks else "Manually Rejected."
            rejected_count += 1

    save_requests(all_requests)

    if approved_count > 0 and rejected_count > 0:
        flash(
            f'Batch {batch_id} partially approved: {approved_count} item(s) approved, {rejected_count} item(s) rejected (insufficient stock).',
            'warning')
    elif approved_count > 0:
        flash(f'Batch {batch_id} fully approved ({approved_count} item(s)).', 'success')
    elif rejected_count > 0:
        flash(f'Batch {batch_id} fully rejected ({rejected_count} item(s)).', 'success')

    return redirect(url_for('admin_dashboard'))


@app.route('/admin/update_purchase_request', methods=['POST'])
@role_required('admin')
def admin_update_purchase_request():
    request_id = int(request.form.get('request_id'))
    new_status = request.form.get('new_status')
    incharge_remarks = request.form.get('incharge_remarks', '').strip()
    admin_user = session['user']

    all_requests = load_requests()
    target_request = next(
        (req for req in all_requests if req['id'] == request_id and req['status'] == 'Pending Incharge'), None)

    if not target_request:
        flash('Error: Purchase request not found or already processed.', 'error')
        return redirect(url_for('admin_dashboard'))

    approval_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    target_request['approval_timestamp'] = approval_time
    target_request['approver_email'] = admin_user['email']
    target_request['incharge_remarks'] = incharge_remarks if incharge_remarks else None

    if new_status == 'Purchased':
        target_request['status'] = 'Purchased'
        flash(f'Purchase request #{request_id} marked as PURCHASED.', 'success')
        audit_logger.info(
            f'PURCHASE by {admin_user["email"]}: Req #{request_id} ({target_request["component_name"]}) marked as Purchased.')
    else:
        target_request['status'] = 'Rejected'
        flash(f'Purchase request #{request_id} rejected by Incharge.', 'success')

    save_requests(all_requests)
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/download_report')
@role_required(['admin', 'hod'])
def admin_download_report():
    all_requests = load_requests()
    if not all_requests:
        flash('No requests to download.', 'error')
        return redirect(url_for('admin_dashboard'))
    output = io.StringIO()
    writer = csv.writer(output)

    # --- ADDED 'Project Type' to headers ---
    headers = [
        'Request ID', 'Batch ID', 'Request Type', 'Project Type', 'Student ID', 'Student name', 'Department',
        'Year of study',
        'Component ID', 'Component Name', 'Quantity', 'Purpose', 'Purchase Link', 'Duration (Days)', 'Status',
        'Mentor Name', 'Mentor Approval', 'Mentor Remarks',
        'HOD Approval', 'HOD Remarks',
        'Incharge Approval', 'Incharge Remarks',
        'Component Issue Time', 'Due date', 'Date of return',
        'Working Returned', 'Not Working Returned', 'Technician Remarks'
    ]
    writer.writerow(headers)
    for req in all_requests:
        writer.writerow([
            req.get('id', 'N/A'), req.get('batch_id', 'N/A'), req.get('request_type', 'borrow'),
            req.get('project_type', 'N/A'),  # --- ADDED 'project_type' field ---
            req.get('student_email', 'N/A'),
            req.get('student_name', 'N/A'), req.get('student_dept', 'N/A'),
            req.get('student_year', 'N/A'), req.get('component_id', 'N/A'),
            req.get('component_name', 'N/A'), req.get('quantity', 'N/A'),
            req.get('project_description', 'N/A'),
            req.get('purchase_link', 'N/A'),
            req.get('duration_days', 'N/A'), req.get('status', 'N/A'),
            req.get('mentor_name', 'N/A'),
            req.get('mentor_approval_timestamp', 'N/A'),
            req.get('mentor_remarks', 'N/A'),
            req.get('hod_approval_timestamp', 'N/A'),
            req.get('hod_remarks', 'N/A'),
            req.get('approver_email', 'N/A'),
            req.get('incharge_remarks', 'N/A'),
            req.get('issue_timestamp', 'N/A'),
            req.get('due_date', 'N/A'), req.get('actual_return_timestamp', 'N/A'),
            req.get('working_count', 'N/A'),
            req.get('not_working_count', 'N/A'),
            req.get('tech_remarks', 'N/A')
        ])

    output.seek(0)
    return Response(
        output, mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=RACE_Lab_Full_Report.csv"}
    )


@app.route('/admin/download_audit_log')
@role_required(['admin', 'hod'])
def admin_download_audit_log():
    approval_pattern = re.compile(r"APPROVAL by (.*?): Req #(\d+). Item (.*?) approved for issue.")
    issue_pattern = re.compile(r"ISSUE by (.*?): Req #(\d+). Stock (.*?) issued (\d+) -> (\d+)")
    collection_pattern = re.compile(r"COLLECTION by (.*?): Req #(\d+). (\d+) working, (\d+) not working.")
    manual_pattern = re.compile(r'MANUAL UPDATE by (.*?): "(.*?)".*?Total: (\d+)->(\d+), Working: (\d+)->(\d+)')
    new_comp_pattern = re.compile(r'NEW COMPONENT by (.*?): "(.*?)".*?Total: (\d+), Working: (\d+)')
    purchase_pattern = re.compile(r'PURCHASE by (.*?): Req #(\d+) \((.*?)\) marked as Purchased.')
    cancel_pattern = re.compile(r'CANCELLED by (.*?): Req #(\d+)')  # Added cancel

    output = io.StringIO()
    writer = csv.writer(output)
    headers = ['Time (IST)', 'Action', 'Performed By', 'Req ID', 'Item', 'Details']
    writer.writerow(headers)

    try:
        with open('audit.log', 'r') as f:
            for line in f:
                try:
                    timestamp, message = line.strip().split(' - ', 1)
                except ValueError:
                    continue

                action, performed_by, req_id, item, details = ('UNKNOWN', 'N/A', 'N/A', 'N/A', 'N/A')

                match_approval = approval_pattern.search(message)
                match_issue = issue_pattern.search(message)
                match_collection = collection_pattern.search(message)
                match_manual = manual_pattern.search(message)
                match_new_comp = new_comp_pattern.search(message)
                match_purchase = purchase_pattern.search(message)
                match_cancel = cancel_pattern.search(message)  # Added cancel

                if match_approval:
                    action = 'APPROVAL (INCHARGE)'
                    performed_by, req_id, item = match_approval.groups()
                    details = "Approved for issue"
                elif match_issue:
                    action = 'ISSUE (TECHNICIAN)'
                    performed_by, req_id, item, qty_from, qty_to = match_issue.groups()
                    details = f"Issued quantity {qty_from} -> {qty_to}"
                elif match_collection:
                    action = 'COLLECTION (RETURN)'
                    performed_by, req_id, working, not_working = match_collection.groups()
                    item = "N/A (See Req ID)"
                    details = f"{working} working, {not_working} not working"
                elif match_manual:
                    action = 'MANUAL UPDATE'
                    performed_by, item, t_from, t_to, w_from, w_to = match_manual.groups()
                    req_id = 'N/A'
                    details = f"Total: {t_from}->{t_to}, Working: {w_from}->{w_to}"
                elif match_new_comp:
                    action = 'NEW COMPONENT'
                    performed_by, item, total, working = match_new_comp.groups()
                    req_id = 'N/A'
                    details = f"Added with Total: {total}, Working: {working}"
                elif match_purchase:
                    action = 'PURCHASE'
                    performed_by, req_id, item = match_purchase.groups()
                    details = "Purchase request marked as complete."
                elif match_cancel:  # Added cancel
                    action = 'CANCELLED'
                    performed_by, req_id = match_cancel.groups()
                    item = "N/A (See Req ID)"
                    details = f"Request cancelled. {message.split('Remarks: ')[-1]}"
                else:
                    details = message

                writer.writerow([timestamp, action, performed_by, req_id, item, details])
    except FileNotFoundError:
        flash('The audit log file was not found.', 'error')
        return redirect(url_for('admin_dashboard'))

    output.seek(0)
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=RACE_Lab_Audit_Log.csv"}
    )


# --- Lab Technician (Lab Assistant) Routes ---
@app.route('/tech')
@role_required('technician')
def tech_dashboard():
    all_requests = load_requests()
    components = get_augmented_components()
    approved_requests = [req for req in all_requests if req['status'] == 'Approved']
    dispatched_requests = [req for req in all_requests if req['status'] == 'ISSUED']
    return render_template('tech_dashboard.html', user=session['user'],
                           approved_requests=approved_requests,
                           dispatched_requests=dispatched_requests,
                           components=components)


@app.route('/tech/dispatch', methods=['POST'])
@role_required('technician')
def tech_dispatch_item():
    request_id = int(request.form.get('request_id'))
    all_requests = load_requests()
    all_components = load_components()
    tech_user_email = session["user"]["email"]

    target_request = next((req for req in all_requests if req['id'] == request_id), None)

    if target_request and target_request['status'] == 'Approved':
        target_component = next((comp for comp in all_components if comp['name'] == target_request['component_name']),
                                None)

        if not target_component:
            flash(f'Error: Component "{target_request["component_name"]}" not found in database. Cannot issue.',
                  'error')
            return redirect(url_for('tech_dashboard'))

        # Re-calculate available stock from the source of truth
        available_stock = target_component['working_quantity'] - target_component['issued_quantity']
        if available_stock < target_request['quantity']:
            flash(
                f'Error: Insufficient stock for "{target_component["name"]}". Only {available_stock} available. Cannot issue.',
                'error')
            return redirect(url_for('tech_dashboard'))

        old_issued = target_component['issued_quantity']
        target_component['issued_quantity'] += target_request['quantity']
        new_issued = target_component['issued_quantity']

        target_request['status'] = 'ISSUED'
        target_request['issue_timestamp'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

        save_components(all_components)
        save_requests(all_requests)

        app.logger.info(f'ITEM ISSUED: Tech "{tech_user_email}" ISSUED req #{request_id}')
        audit_logger.info(
            f'ISSUE by {tech_user_email}: Req #{request_id}. Stock {target_component["name"]} issued {old_issued} -> {new_issued}')

        flash(f'Request #{request_id} marked as ISSUED. Inventory updated.', 'success')
    else:
        flash('Error: Could not Issue request. Not found or not in "Approved" state.', 'error')

    return redirect(url_for('tech_dashboard'))


@app.route('/tech/collect_form/<int:request_id>', methods=['GET', 'POST'])
@role_required('technician')
def tech_collect_item_form(request_id):
    tech_user = session['user']
    all_requests = load_requests()
    all_components = load_components()

    target_request = next((req for req in all_requests if req['id'] == request_id), None)

    if not target_request:
        abort(404)

    if target_request['status'].lower() != 'issued':
        flash('Error: This item is not in "ISSUED" state. Cannot collect.', 'error')
        return redirect(url_for('tech_dashboard'))

    target_component = next((comp for comp in all_components if comp['name'] == target_request['component_name']), None)

    if request.method == 'POST':
        try:
            working_count = int(request.form.get('working_count'))
            not_working_count = int(request.form.get('not_working_count'))
            tech_remarks = request.form.get('tech_remarks', '').strip()
            total_returned = working_count + not_working_count

            if total_returned != target_request['quantity']:
                flash(f"Error: Total items ({total_returned}) does not match issued ({target_request['quantity']}).",
                      'error')
                return redirect(url_for('tech_collect_item_form', request_id=request_id))

            if target_component:
                # 1. Decrease issued quantity
                target_component['issued_quantity'] -= total_returned

                # 2. Add to not_working_quantity
                target_component['not_working_quantity'] += not_working_count

                # 3. Recalculate working_quantity based on total
                target_component['working_quantity'] = target_component['total_quantity'] - target_component[
                    'not_working_quantity']

                if target_component['issued_quantity'] < 0: target_component['issued_quantity'] = 0

                audit_logger.info(
                    f'COLLECTION by {tech_user["email"]}: Req #{request_id}. {working_count} working, {not_working_count} not working.')
                save_components(all_components)

            else:
                audit_logger.warning(
                    f'COLLECTION (No Stock Update): Tech {tech_user["email"]} collected Req #{request_id} but component "{target_request["component_name"]}" not in DB.')

            target_request['status'] = 'Returned'
            target_request['actual_return_timestamp'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            target_request['working_count'] = working_count
            target_request['not_working_count'] = not_working_count
            target_request['tech_remarks'] = tech_remarks if tech_remarks else "N/A"

            save_requests(all_requests)
            flash(f'Request #{request_id} marked as Returned. Inventory updated.', 'success')
            return redirect(url_for('tech_dashboard'))

        except ValueError:
            flash('Error: Invalid count. Please enter numbers.', 'error')
            return redirect(url_for('tech_collect_item_form', request_id=request_id))
        except Exception as e:
            flash(f'An error occurred: {e}', 'error')
            return redirect(url_for('tech_collect_item_form', request_id=request_id))

    return render_template('tech_collect_form.html',
                           user=tech_user,
                           request=target_request)


@app.route('/tech/add_inventory', methods=['POST'])
@role_required('technician')
def tech_add_inventory():
    tech_user = session['user']
    new_id = request.form.get('new_component_id').upper()
    new_name = request.form.get('new_component_name')
    new_total = int(request.form.get('new_total'))
    new_working = int(request.form.get('new_working'))

    if new_total < new_working:
        flash('Error: "Working" count cannot be greater than "Total" count.', 'error')
        return redirect(url_for('tech_dashboard'))

    all_components = load_components()

    for comp in all_components:
        if comp['id'] == new_id:
            flash(f'Error: Component ID "{new_id}" already exists.', 'error')
            return redirect(url_for('tech_dashboard'))
        if comp['name'].lower() == new_name.lower():
            flash(f'Error: Component Name "{new_name}" already exists.', 'error')
            return redirect(url_for('tech_dashboard'))

    new_component = {
        "id": new_id,
        "name": new_name,
        "total_quantity": new_total,
        "working_quantity": new_working,
        "not_working_quantity": new_total - new_working,
        "issued_quantity": 0
    }
    all_components.append(new_component)
    save_components(all_components)

    audit_logger.info(
        f'NEW COMPONENT by {tech_user["email"]}: "{new_name}". Total: {new_total}, Working: {new_working}')
    flash(f'Success: Component "{new_name}" has been added.', 'success')
    return redirect(url_for('tech_dashboard'))


@app.route('/tech/update_inventory', methods=['POST'])
@role_required('technician')
def tech_update_inventory():
    component_name = request.form.get('component_name')
    new_total = int(request.form.get('new_total'))
    new_working = int(request.form.get('new_working'))
    tech_user = session['user']

    all_components = load_components()
    target_component = next((comp for comp in all_components if comp['name'] == component_name), None)

    if target_component:
        old_total = target_component['total_quantity']
        old_working = target_component['working_quantity']

        if new_working > new_total:
            flash(f'Error: New working count ({new_working}) cannot be greater than new total ({new_total}).', 'error')
            return redirect(url_for('tech_dashboard'))

        current_issued = target_component['issued_quantity']

        # This is the key check
        if (new_working - current_issued) < 0:
            flash(
                f'Error: New working count ({new_working}) is less than current issued count ({current_issued}). Please collect items first.',
                'error')
            return redirect(url_for('tech_dashboard'))

        target_component['total_quantity'] = new_total
        target_component['working_quantity'] = new_working
        target_component['not_working_quantity'] = new_total - new_working

        audit_logger.info(
            f'MANUAL UPDATE by {tech_user["email"]}: "{component_name}". Total: {old_total}->{new_total}, Working: {old_working}->{new_working}')
        save_components(all_components)
        flash(f'Inventory for "{component_name}" updated.', 'success')
    else:
        flash('Error: Component not found.', 'error')
    return redirect(url_for('tech_dashboard'))


# --- Run App ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, debug=True)