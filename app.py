import eventlet
eventlet.monkey_patch()  # ✅ This must be the first line!
from flask import Flask, render_template, request, redirect, session, flash, url_for, jsonify, send_from_directory, make_response
from flask_socketio import  send, emit, join_room
from flask_socketio import SocketIO
from flask_cors import CORS
from flask_mail import Mail, Message
import random
from werkzeug.utils import secure_filename
import os
# from datetime import timedelta
import datetime
# from datetime import datetime


import base64
from io import BytesIO
from PIL import Image
import re
from functools import wraps
import threading

from flask_mysqldb import MySQL
import MySQLdb

import cloudinary
import cloudinary.uploader
import cloudinary.api

from dotenv import load_dotenv








# ⚙️ App setup
app = Flask(__name__)
app.config['SECRET_KEY'] = 'c061cfd9aff94c6998854f4fcdd835cf49c0182dad5fb3d9ad16eaa9645d121d'



CORS(
    app,
    origins=[
        "https://chat.meixup.in"
    ],
    supports_credentials=True,
)

socketio = SocketIO(app, cors_allowed_origins=["https://chat.meixup.in"
                                               ] , async_mode='eventlet')

print("Async mode:", socketio.async_mode)






load_dotenv()  # ✅ Load env vars from .env file




def validate_email(email):
    return re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', email)


def validate_username(username):
    return re.match(r'^[a-zA-Z0-9_]{3,20}$', username)


ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
MAX_FILE_SIZE_MB = 3


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def upload_to_cloudinary(buffer, username, result_dict):
    try:
        # ✅ Reconfigure Cloudinary in the thread
        cloudinary.config(
            cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME"),
            api_key=os.environ.get("CLOUDINARY_API_KEY"),
            api_secret=os.environ.get("CLOUDINARY_API_SECRET")
        )

        upload_result = cloudinary.uploader.upload(
            buffer,
            public_id=f"profile_pics/{username}",
            overwrite=True,
            resource_type="image"
        )
        result_dict['url'] = upload_result.get("secure_url", "https://res.cloudinary.com/dcfofc9fa/image/upload/v1753272438/profile_pic_sujsef.png")
    except Exception as e:
        print("Background upload failed:", e)
        result_dict['url'] = "https://res.cloudinary.com/dcfofc9fa/image/upload/v1753272438/profile_pic_sujsef.png"


def save_cropped_image(base64_str, username):
    if not base64_str or not username:
        return 'https://res.cloudinary.com/dcfofc9fa/image/upload/v1753272438/profile_pic_sujsef.png'

    try:
        if base64_str.startswith('data:image'):
            base64_str = base64_str.split(',', 1)[1]

        image_data = base64.b64decode(base64_str)
        image = Image.open(BytesIO(image_data))

        if image.format.lower() not in ALLOWED_EXTENSIONS:
            raise ValueError("Unsupported image format")

        buffer = BytesIO()
        image.save(buffer, format=image.format)
        buffer.seek(0)

        if len(buffer.getvalue()) > MAX_FILE_SIZE_MB * 1024 * 1024:
            raise ValueError("Image exceeds 3MB size limit")

        result = {}
        upload_thread = threading.Thread(target=upload_to_cloudinary, args=(buffer, username, result))
        upload_thread.start()
        upload_thread.join()  # remove this if you want async

        return result.get('url', 'https://res.cloudinary.com/dcfofc9fa/image/upload/v1753272438/profile_pic_sujsef.png')

    except Exception as e:
        print("Image upload failed:", e)
        return "https://res.cloudinary.com/dcfofc9fa/image/upload/v1753272438/profile_pic_sujsef.png"




# Flask MySQL configuration
app.config['MYSQL_HOST'] = 'ballast.proxy.rlwy.net'
app.config['MYSQL_PORT'] = 10272  # ✅ Add this line
app.config['MYSQL_USER'] = 'root'
app.config['MYSQL_PASSWORD'] = 'asOxKgVNXYPBbZiHGbmPLjaCxvWJLNlr'
app.config['MYSQL_DB'] = 'railway'

mysql = MySQL(app)

# Manual MySQLdb connection for dashboard

db = MySQLdb.connect(
    host="ballast.proxy.rlwy.net",
    port=10272,  # ✅ Add this
    user="root",
    passwd="asOxKgVNXYPBbZiHGbmPLjaCxvWJLNlr",
    db="railway"
)
cursor = db.cursor()








# Flask-Mail configuration
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'otroom101@gmail.com'
app.config['MAIL_PASSWORD'] = 'wrqy cvhl lrta ztre'
mail = Mail(app)




# def login_required(f):

#     @wraps(f)
#     def decorated_function(*args, **kwargs):
#         if 'user_email' not in session:
#             return jsonify({
#                 "status": "error",
#                 "message": "Unauthorized access. Please login first."
#             }), 401
#         return f(*args, **kwargs)

#     return decorated_function


app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="None",  # or 'Strict'
    SESSION_COOKIE_SECURE=False  # Set to True if using HTTPS
)


# ✅ Automatically refresh session expiration on each request
@app.before_request
def before_request():
    session.permanent = True
    session.modified = True






# ✅ Redirect root to login page
# @app.route('/')
# def index():
#     return send_from_directory('../frontend', 'index.html')

# 📄 Optional route
@app.route('/')
def index():
    return "<h2>Flask-SocketIO is running!</h2>"


# login
@app.route('/login', methods=['POST' , 'OPTIONS'])
# @cross_origin(origins="https://chat.meixup.in", supports_credentials=True)
def login():
    if request.method == 'OPTIONS':
        return '', 200  # Preflight response
    data = request.json
    email = data.get('email')
    password = data.get('password')

    cursor = mysql.connection.cursor()
    cursor.execute("SELECT username, email, role, email_verified FROM users WHERE email = %s AND password = %s", (email, password))
    user = cursor.fetchone()
    cursor.close()

    if user:
        username, email, role, email_verified = user

        if not email_verified:
            return jsonify({"status": "error", "message": "Please verify your email with OTP first."})

        # session['user_email'] = email   # ✅ This line MUST execute
        # session.permanent = True        # Optional, sets expiration

        # print("Logged in:", session)    # ✅ Debugging

        return jsonify({
            "status": "success",
            "message": "Login successful",
            "user": {
                "username": username,
                "email": email,
                "role": role
            }
        })

    return jsonify({"status": "error", "message": "Invalid credentials"})





from threading import Thread


# Background task to send email
def send_otp_email_async(app, email, otp):
    with app.app_context():
        try:
            msg = Message('OTP Verification', sender=app.config['MAIL_USERNAME'], recipients=[email])
            msg.body = f"Your OTP is: {otp}\nIt will expire in 10 minutes."
            mail.send(msg)
            print(f"📧 OTP sent to {email}")
        except Exception as mail_error:
            app.logger.error(f"❌ Failed to send OTP email: {mail_error}")


@app.route('/register', methods=['POST', 'OPTIONS'])
# @cross_origin(origins="https://chat.meixup.in", supports_credentials=True)
def register():
    try:
        data = request.form
        username = data.get('username')
        email = data.get('email')
        password = data.get('password')
        nickname = data.get('nickname')
        gender = data.get('gender')
        dob = data.get('dob')
        profile_pic_base64 = data.get('cropped_image')
        profile_pic_thumb = data.get('profile_pic_thumb', None)
        otp = str(random.randint(100000, 999999))
        otp_expiry = datetime.datetime.now() + datetime.timedelta(minutes=10)

        profile_pic = save_cropped_image(profile_pic_base64, username)
        print("✅ Saved profile image as:", profile_pic)

        cursor = mysql.connection.cursor()

        # Check if already registered
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        if cursor.fetchone():
            cursor.close()
            return jsonify({"status": "error", "message": "Email already registered."}), 400

        # Remove old pending registration
        cursor.execute("DELETE FROM pending_users WHERE email = %s", (email,))

        # Insert new user into pending_users
        cursor.execute("""
            INSERT INTO pending_users (
                nickname, gender, username, password, email,
                profile_pic, profile_pic_thumb, otp_code, otp_expiry, dob
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            nickname, gender, username, password, email,
            profile_pic, profile_pic_thumb, otp, otp_expiry, dob
        ))
        mysql.connection.commit()
        cursor.close()

        # ✅ Return success before sending email
        response = make_response(jsonify({
            "status": "success",
            "message": "OTP sent to your email. Please verify to complete registration."
        }), 200)
        response.headers['Content-Type'] = 'application/json'
        print("✅ Sending JSON response")
        
        # 📤 Start background thread for email
        Thread(target=send_otp_email_async, args=(app, email, otp)).start()

        return response

    except Exception as e:
        app.logger.error("❌ Registration failed: %s", e)
        return jsonify({
            "status": "error",
            "message": f"Server error: {str(e)}"
        }), 500


# otp verify
@app.route('/verify-otp', methods=['GET', 'POST', 'OPTIONS'])
# @cross_origin(origins="https://chat.meixup.in", supports_credentials=True)
def verify_otp():
    if request.method == 'OPTIONS':
        return '', 200  # Preflight response
    data = request.json
    email = data.get('email')
    otp = data.get('otp')

    cursor = mysql.connection.cursor()

    # Check pending user with email and otp_code
    cursor.execute("SELECT * FROM pending_users WHERE email = %s AND otp_code = %s", (email, otp))
    pending_user = cursor.fetchone()

    if pending_user:
    # Insert into users table using values from pending_users
        cursor.execute("""
            INSERT INTO users (
                nickname, gender, username, password, email, profile_pic, profile_pic_thumb,
                email_verified, otp_code, otp_expiry, dob
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                pending_user[1],  # nickname
                pending_user[2],  # gender
                pending_user[3],  # username
                pending_user[4],  # password
                pending_user[5],  # email
                pending_user[6],  # profile_pic
                pending_user[7],  # profile_pic_thumb
                1,  # email_verified (hardcoded to True)
                pending_user[9],  # otp_code
                pending_user[10],  # otp_expiry
                pending_user[12],  # dob
            ))  

            # Delete the verified entry from pending_users
        cursor.execute("DELETE FROM pending_users WHERE email = %s", (email,))

            # Commit changes
        mysql.connection.commit()
        cursor.close()

        return jsonify({"status": "success", "message": "OTP verified and user registered."})

    else:
        cursor.close()
        return jsonify({"status": "error", "message": "Invalid OTP."})


# resend otp
@app.route('/resend-otp', methods=['GET', 'POST', 'OPTIONS'])
# @cross_origin(origins="https://chat.meixup.in", supports_credentials=True)
def resend_otp():
    if request.method == 'OPTIONS':
        return '', 200  # Preflight response
    data = request.json
    email = data.get('email')
    otp = random.randint(100000, 999999)

    cursor = mysql.connection.cursor()
    cursor.execute("UPDATE pending_users SET otp_code = %s WHERE email = %s", (otp, email))
    mysql.connection.commit()
    cursor.close()

    msg = Message('Resend OTP', sender=app.config['MAIL_USERNAME'], recipients=[email])
    msg.body = f"Your new OTP is: {otp}"
    mail.send(msg)

    return jsonify({"status": "success", "message": "OTP resent successfully."})


# forget password
@app.route('/forgot-password', methods=['GET', 'POST', 'OPTIONS'])
# @cross_origin(origins="https://chat.meixup.in", supports_credentials=True)
def forgot_password():
    if request.method == 'OPTIONS':
        return '', 200  # Preflight response
    data = request.json
    email = data.get('email')

    cursor = mysql.connection.cursor()
    cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
    user = cursor.fetchone()

    if user:
        otp = random.randint(100000, 999999)
        cursor.execute("UPDATE users SET otp_code = %s WHERE email = %s", (otp, email))
        mysql.connection.commit()
        cursor.close()

        msg = Message('Password Reset OTP', sender=app.config['MAIL_USERNAME'], recipients=[email])
        msg.body = f"Your OTP for password reset is: {otp}"
        mail.send(msg)

        return jsonify({"status": "success", "message": "OTP sent for password reset."})
    else:
        cursor.close()
        return jsonify({"status": "error", "message": "Email not registered."})


# reset password
@app.route('/reset-password', methods=['GET', 'POST', 'OPTIONS'])
# @cross_origin(origins="https://chat.meixup.in", supports_credentials=True)
def reset_password():
    if request.method == 'OPTIONS':
        return '', 200  # Preflight response
    data = request.json
    email = data.get('email')
    otp = data.get('otp')
    new_password = data.get('new_password')

    cursor = mysql.connection.cursor()
    cursor.execute("SELECT * FROM users WHERE email = %s AND otp_code = %s", (email, otp))
    user = cursor.fetchone()

    if user:
        cursor.execute("UPDATE users SET password = %s WHERE email = %s", (new_password, email))
        mysql.connection.commit()
        cursor.close()
        return jsonify({"status": "success", "message": "Password reset successful."})
    else:
        cursor.close()
        return jsonify({"status": "error", "message": "Invalid OTP or email."})






@app.route('/api/user/by-email', methods=['GET', 'OPTIONS'])
def get_user_by_email():
    if request.method == 'OPTIONS':
        return '', 200  # Preflight response for CORS

    email = request.args.get('email')
    if not email:
        return jsonify({"success": False, "message": "Email is missing"}), 400

    try:
        cursor = mysql.connection.cursor()
        query = """
            SELECT nickname, email, username, gender, age, profile_pic, created_at
            FROM users
            WHERE email = %s
        """
        cursor.execute(query, (email,))
        user = cursor.fetchone()

        if not user:
            return jsonify({"success": False, "message": "User not found"}), 404

        print(f"📧 Found user with email: {user[1]}")

        return jsonify({"success": True, "user": user})

    except Exception as e:
        print(f"❌ Database error: {e}")
        return jsonify({"success": False, "message": "Internal server error"}), 500

    # Close the cursor
    finally:
        if 'cursor' in locals():
            cursor.close()




# user_list
# API to get all users (shuffled)
@app.route('/api/users', methods=['GET', 'OPTIONS'])
# @cross_origin(origins="https://chat.meixup.in", supports_credentials=True)
def get_users():
    if request.method == 'OPTIONS':
        return '', 200
    cursor.execute("SELECT username, profile_pic FROM users WHERE email_verified = 1")
    result = cursor.fetchall()
    users = [{'username': row[0], 'profile_pic': row[1]} for row in result]
    random.shuffle(users)
    return jsonify(users)



# # -------------------------------
# # ✅ API: Get Chat Messages
# # -------------------------------
# Get messages between two users
@app.route('/api/messages/<receiver>')
# @cross_origin(origins="https://chat.meixup.in", supports_credentials=True)
def get_messages(receiver):
    sender = request.args.get('from')
    if not sender or not receiver:
        return jsonify({'error': 'Missing sender or receiver'}), 400

    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT sender, receiver, message, timestamp
        FROM messages
        WHERE (sender=%s AND receiver=%s) OR (sender=%s AND receiver=%s)
        ORDER BY timestamp ASC
    """, (sender, receiver, receiver, sender))
    
    rows = cur.fetchall()
    cur.close()

    messages = []
    for row in rows:
        messages.append({
            'sender': row[0],
            'receiver': row[1],
            'message': row[2],
            'timestamp': str(row[3])  # convert timestamp to string if needed
        })

    return jsonify(messages)

# # -------------------------------
# # ✅ WebSocket: Join Room
# # -------------------------------
# WebSocket: Join Room
@socketio.on('join_room')
def on_join(data):
    room = data.get('room')
    if room:
        join_room(room)
        print(f"User joined room: {room}")

# # -------------------------------
# # ✅ WebSocket: Send Message
# # -------------------------------
# WebSocket: Send Message
@socketio.on('send_message')
def handle_send_message(data):
    sender = data.get('sender')
    receiver = data.get('receiver')
    message = data.get('message')
    room = data.get('room')

    if not all([sender, receiver, message, room]):
        return

    try:
        cur = mysql.connection.cursor()
        cur.execute("""
            INSERT INTO messages (sender, receiver, message)
            VALUES (%s, %s, %s)
        """, (sender, receiver, message))
        mysql.connection.commit()
        cur.close()

        emit('receive_message', data, room=room)
    except Exception as e:
        print(f"Error saving message: {e}")
        
        




# Route to serve user profile pictures
@app.route('/static/uploads/<filename>')
def serve_profile_pic(filename):
    return send_from_directory('static/uploads', filename)



    




# ✅ API: Get Recent Users (message.html)
@app.route('/api/recent-users', methods=['GET', 'OPTIONS'])
def recent_users():
    try:
        current_user = request.args.get('user')
        if not current_user:
            return jsonify({'error': 'Missing user parameter'}), 400

        cursor = mysql.connection.cursor()

        query = """
            SELECT u.username, u.profile_pic, u.is_active, MAX(m.timestamp) as last_msg_time
            FROM users u
            JOIN messages m ON (
                (m.sender = %s AND m.receiver = u.username) OR
                (m.receiver = %s AND m.sender = u.username)
            )
            WHERE u.username != %s
            GROUP BY u.username, u.profile_pic, u.is_active
            ORDER BY last_msg_time DESC
            LIMIT 10;
        """
        cursor.execute(query, (current_user, current_user, current_user))
        results = cursor.fetchall()
        cursor.close()

        users = []
        for row in results:
            users.append({
                'username': row[0],
                'profile_pic': row[1],
                'is_active': row[2] == 1
            })

        return jsonify(users)

    except Exception as e:
        import traceback
        print("Error in /api/recent-users:", traceback.format_exc())
        return jsonify({'error': 'Internal server error'}), 500
    
    
# Store online users
online_users = set()


# Get global chat messages from the database
@app.route('/api/global_messages')
def get_global_messages():
    cursor = mysql.connection.cursor()
    cursor.execute("SELECT sender, message, timestamp FROM global_message ORDER BY timestamp ASC")
    messages = cursor.fetchall()
    cursor.close()

    formatted_messages = [
        {
            'sender': msg[0],
            'message': msg[1],
            'timestamp': msg[2].isoformat() if isinstance(msg[2], datetime.datetime) else str(msg[2])
        }
        for msg in messages
    ]
    return jsonify(formatted_messages)




# till now all correct

# online user
@app.route('/api/users/online', methods=['GET'])  # Changed POST to GET
def get_online_users():
    cursor = mysql.connection.cursor()
    cursor.execute("SELECT username, profile_pic FROM users WHERE is_active=1")
    users = cursor.fetchall()
    cursor.close()
    
    return jsonify({"success": True, "user": users})


@socketio.on('connect')
def handle_connect():
    email = request.args.get('email')
    cursor = mysql.connection.cursor()
    cursor.execute("SELECT username FROM users WHERE email=%s", (email,))
    user = cursor.fetchone()
    if user:
        online_users.add(user[0])
        emit('user_online', {'username': user[0]}, broadcast=True)


@socketio.on('disconnect')
def handle_disconnect():
    try:
        email = request.args.get('email')
        cursor = mysql.connection.cursor()
        cursor.execute("SELECT username FROM users WHERE email=%s", (email,))
        user = cursor.fetchone()
        cursor.close()
        if user:
            online_users.discard(user[0])
            emit('user_offline', {'username': user[0]}, broadcast=True)
    except Exception as e:
        print("Disconnect error:", str(e))


@socketio.on('global_message')
def handle_global_message(data):
    sender = data.get('sender')
    message = data.get('message')
    cursor = mysql.connection.cursor()
    cursor.execute("INSERT INTO global_message (sender, message) VALUES (%s, %s)", (sender, message))
    mysql.connection.commit()
    cursor.close()
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    emit('new_message', {'sender': sender, 'message': message, 'timestamp': timestamp}, broadcast=True)

# logout
@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"success": True, "message": "Logged out successfully"}), 200

if __name__ == '__main__':
    socketio.run(app, debug=True)
