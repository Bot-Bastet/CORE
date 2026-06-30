/*
 * SpotBot Controller — Arduino Mega v3.1
 * =========================================
 * IMU : BNO085 uniquement (I2C, adresse 0x4A)
 * Servos : 12x MG996R (D2-D13, alim externe 6V/10A)
 * Sonar  : HC-SR04 (TRIG=D22, ECHO=D23) — optionnel
 *
 * JSON emis (50 Hz):
 * {
 *   "imu":{
 *     "qw":10000,"qx":0,"qy":0,"qz":0,  ← quaternion * 10000
 *     "lax":0,"lay":0,"laz":0,           ← accél linéaire cm/s² * 100
 *     "gx":0,"gy":0,"gz":0,              ← gyro mrad/s * 1000
 *     "calib":3                          ← calibration 0-3 (3=parfait)
 *   },
 *   "sonar":{"dist_cm":42.5,"valid":true,"alert":false}
 * }
 *
 * JSON recu:
 *   {"servos":[90,90,...]}   (12 angles 0-180°)
 *   {"cmd":"stand"}          (stand | sit | stop | reset_imu)
 *
 * BRANCHEMENTS:
 *   Servos D2-D13  — alim externe 6V/10A (GND commun Arduino)
 *   BNO085 SDA→20, SCL→21, VCC→3.3V, GND, INT→D18, RST→D19
 *             PS0→GND, PS1→GND (adresse 0x4A)
 *   HC-SR04 TRIG→D22, ECHO→D23, VCC→5V, GND
 *
 * LIBRAIRIE:
 *   arduino-cli lib install "SparkFun BNO08x"
 */

#include <Arduino.h>
#include <Servo.h>
#include <Wire.h>
#include <SparkFun_BNO08x_Arduino_Library.h>

// ============================================================
// Configuration
// ============================================================
#define SKETCH_VERSION    "v0.2.7"
#define NUM_SERVOS        12
#define SERIAL_BAUD       500000
#define IMU_PUBLISH_MS    50      // 20 Hz
#define WATCHDOG_MS       3000
#define JSON_BUFFER_SIZE  320
#define SERVO_SPEED       1.0f    // deg/loop (~50 deg/s a 50Hz) — bon compromis visuel/securite

// ---- Pins servos (D2-D13) ----
const uint8_t SERVO_PINS[NUM_SERVOS] = {2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13};

// ---- BNO085 ----
#define BNO085_INT_PIN  18
#define BNO085_RST_PIN  19
#define BNO085_ADDR     0x4A

// ---- HC-SR04 ----
// Sonar optionnel — activable via #define
#define SONAR_ENABLED false   // Mettre a true si le HC-SR04 est installe
#define SONAR_TRIG_PIN  22
#define SONAR_ECHO_PIN  23
#define SONAR_ALERT_CM  30.0f
#define SONAR_MAX_CM    400.0f
#define SONAR_MIN_CM    2.0f
#define SONAR_SAMPLES   3

// ---- Positions servo ----
const float SERVO_STAND[NUM_SERVOS] = {90,90,90, 90,90,90, 90,90,90, 90,90,90};
const float SERVO_SIT[NUM_SERVOS]   = {90,120,60, 90,120,60, 90,120,60, 90,120,60};
#define SERVO_MIN 0
#define SERVO_MAX 180

// ============================================================
// Variables globales
// ============================================================
Servo  servos[NUM_SERVOS];
float  servo_targets[NUM_SERVOS];
float  servo_current[NUM_SERVOS];
char   json_buf[JSON_BUFFER_SIZE];
int    json_pos = 0;
bool   bno_ok   = false;

unsigned long last_cmd_ms  = 0;
unsigned long last_imu_ms  = 0;
bool          watchdog_mode = false;
bool          servos_enabled = false;  // servos désactivés jusqu'à la 1ère commande

BNO08x bno;

struct BnoData {
    float qw = 1, qx = 0, qy = 0, qz = 0;
    float lax = 0, lay = 0, laz = 0;
    float gx = 0,  gy = 0,  gz = 0;
    uint8_t calib = 0;
} bno_data;

// Filtre sonar
float sonar_history[SONAR_SAMPLES] = {0};
int   sonar_idx   = 0;
bool  sonar_valid = false;
unsigned long last_sonar_ms = 0;
float cached_sonar_dist = -1.0f;

// ============================================================
// Setup
// ============================================================
void setup() {
    // ⚠️ URGENCE : forcer TOUS les pins servos à LOW IMMÉDIATEMENT
    // pour éviter tout twitching parasite pendant le boot.
    for (int i = 0; i < NUM_SERVOS; i++) {
        pinMode(SERVO_PINS[i], OUTPUT);
        digitalWrite(SERVO_PINS[i], LOW);
        servo_targets[i] = SERVO_STAND[i];
        servo_current[i] = SERVO_STAND[i];
    }
    delay(50);  // Stabilisation des signaux avant d'initialiser le reste

    Serial.begin(SERIAL_BAUD);
    delay(100);

    // I2C — 400 kHz Fast Mode
    Wire.begin();
    Wire.setClock(400000);

    // print debug
    Serial.println("{\"boot\":\"pre-init\"}");
    Serial.flush();

    // BNO085
    pinMode(BNO085_INT_PIN, INPUT_PULLUP);

    bno_ok = bno.begin(BNO085_ADDR, Wire);
    if (bno_ok) {
        bno.enableRotationVector(20);
        bno.enableLinearAccelerometer(20);
        bno.enableGyro(20);
        Serial.println("{\"boot\":\"SpotBot v3.1\",\"bno085\":true}");
    } else {
        Serial.println("{\"boot\":\"SpotBot v3.1\",\"bno085\":false,\"error\":\"BNO085 non detecte — verifiez I2C et adresse 0x4A\"}");
    }

    // HC-SR04
    pinMode(SONAR_TRIG_PIN, OUTPUT);
    pinMode(SONAR_ECHO_PIN, INPUT);
    digitalWrite(SONAR_TRIG_PIN, LOW);

    last_cmd_ms = millis();
    Serial.print("{\"status\":\"ready\",\"bno085\":");
    Serial.print(bno_ok ? "true" : "false");
    Serial.println(",\"sonar\":true}");
}

// ============================================================
// Loop
// ============================================================
void loop() {
    readSerial();

    if (!watchdog_mode && (millis() - last_cmd_ms) > WATCHDOG_MS) {
        watchdog_mode = true;
        // Servos déjà libres si jamais activés, sinon on remet en stand
        if (servos_enabled) {
            setStand();
            Serial.println("{\"watchdog\":\"stand\"}");
        }
    }

    applyServos();

    if (bno_ok) readBNO085();

#if SONAR_ENABLED
    // Sonar actif — lire et filtrer
    cached_sonar_dist = readSonar();
#else
    cached_sonar_dist = -1.0f;
    sonar_valid = false;
#endif

    if ((millis() - last_imu_ms) >= IMU_PUBLISH_MS) {
        last_imu_ms = millis();
        publishAll(cached_sonar_dist);
    }
}

// ============================================================
// BNO085
// ============================================================
void readBNO085() {
    if (bno.wasReset()) {
        bno.enableRotationVector(20);
        bno.enableLinearAccelerometer(20);
        bno.enableGyro(20);
        Serial.println("{\"warn\":\"BNO085 reset — re-init\"}");
    }

    if (!bno.getSensorEvent()) return;

    switch (bno.getSensorEventID()) {
        case SENSOR_REPORTID_ROTATION_VECTOR: {
            float sqw = bno.getQuatReal();
            float sqx = bno.getQuatI();
            float sqy = bno.getQuatJ();
            float sqz = bno.getQuatK();
            // Appliquer une rotation de 180° autour de X (IMU montée à l'envers sous le robot)
            bno_data.qw    = sqx;
            bno_data.qx    = -sqw;
            bno_data.qy    = -sqz;
            bno_data.qz    = sqy;
            bno_data.calib = bno.getQuatAccuracy();
            break;
        }
        case SENSOR_REPORTID_LINEAR_ACCELERATION:
            bno_data.lax = bno.getLinAccelX();
            bno_data.lay = -bno.getLinAccelY();
            bno_data.laz = -bno.getLinAccelZ();
            break;
        case SENSOR_REPORTID_GYROSCOPE_CALIBRATED:
            bno_data.gx = bno.getGyroX();
            bno_data.gy = -bno.getGyroY();
            bno_data.gz = -bno.getGyroZ();
            break;
    }
}

// ============================================================
// Serial JSON parser
// ============================================================
void readSerial() {
    while (Serial.available() > 0) {
        char c = Serial.read();
        if (c == '\n' || c == '\r') {
            if (json_pos > 0) {
                json_buf[json_pos] = '\0';
                parseJSON(json_buf);
                json_pos = 0;
            }
        } else if (json_pos < JSON_BUFFER_SIZE - 1) {
            json_buf[json_pos++] = c;
        }
    }
}

void parseJSON(const char* json) {
    int len = strlen(json);
    while (len > 0 && (json[len - 1] == ' ' || json[len - 1] == '\t' || json[len - 1] == '\r' || json[len - 1] == '\n')) {
        len--;
    }
    if (len < 2 || json[len - 1] != '}') {
        return;
    }
    last_cmd_ms   = millis();
    watchdog_mode = false;
    if (strstr(json, "\"servos\""))       parseServos(json);
    else if (strstr(json, "\"cmd\""))     parseCmd(json);
}

void parseServos(const char* json) {
    const char* s = strchr(json, '[');
    if (!s) return;
    float angles[NUM_SERVOS]; int n = 0;
    char* p = (char*)(s + 1);
    while (n < NUM_SERVOS && *p && *p != ']') {
        while (*p == ' ' || *p == ',') p++;
        if (*p == ']') break;
        angles[n++] = atof(p);
        while (*p && *p != ',' && *p != ']') p++;
    }
    if (n == NUM_SERVOS) {
        servos_enabled = true;
        for (int i = 0; i < NUM_SERVOS; i++) {
            if (!servos[i].attached()) {
                servos[i].attach(SERVO_PINS[i]);
                servo_current[i] = constrain(angles[i], SERVO_MIN, SERVO_MAX);
            }
            servo_targets[i] = constrain(angles[i], SERVO_MIN, SERVO_MAX);
        }
    }
}

float parseNumAfterKey(const char* json, const char* key) {
    const char* p = strstr(json, key);
    if (!p) return -999.0f;
    p += strlen(key);
    while (*p && (*p == ' ' || *p == ':' || *p == '"' || *p == '\t')) {
        p++;
    }
    return atof(p);
}

void parseCmd(const char* json) {
    if (strstr(json, "\"stand\""))          setStand();
    else if (strstr(json, "\"sit\""))       setSit();
    else if (strstr(json, "\"stop\""))      stopServos();
    else if (strstr(json, "\"reset_imu\"")) resetBNO085();
    else if (strstr(json, "\"attach\"")) {
        float val = parseNumAfterKey(json, "\"index\"");
        if (val != -999.0f) {
            int idx = (int)val;
            if (idx >= 0 && idx < NUM_SERVOS) {
                if (!servos[idx].attached()) servos[idx].attach(SERVO_PINS[idx]);
                servos_enabled = true;
            }
        }
    }
    else if (strstr(json, "\"detach\"")) {
        float val = parseNumAfterKey(json, "\"index\"");
        if (val != -999.0f) {
            int idx = (int)val;
            if (idx >= 0 && idx < NUM_SERVOS) {
                servos[idx].detach();
                pinMode(SERVO_PINS[idx], OUTPUT);
                digitalWrite(SERVO_PINS[idx], LOW);
            }
        }
    }
    else if (strstr(json, "\"write\"")) {
        float idx_val = parseNumAfterKey(json, "\"index\"");
        float ang_val = parseNumAfterKey(json, "\"angle\"");
        if (idx_val != -999.0f && ang_val != -999.0f) {
            int idx = (int)idx_val;
            float ang = ang_val;
            if (idx >= 0 && idx < NUM_SERVOS) {
                if (!servos[idx].attached()) servos[idx].attach(SERVO_PINS[idx]);
                servo_targets[idx] = constrain(ang, SERVO_MIN, SERVO_MAX);
                servos_enabled = true;
            }
        }
    }
}

void resetBNO085() {
    digitalWrite(BNO085_RST_PIN, LOW);
    delay(10);
    digitalWrite(BNO085_RST_PIN, HIGH);
    delay(300);
    bno.enableRotationVector(20);
    bno.enableLinearAccelerometer(20);
    bno.enableGyro(20);
    Serial.println("{\"info\":\"BNO085 reset\"}");
}

void setStand() {
    servos_enabled = true;
    for (int i = 0; i < NUM_SERVOS; i++) {
        if (!servos[i].attached()) servos[i].attach(SERVO_PINS[i]);
        servo_targets[i] = SERVO_STAND[i];
    }
    Serial.println("{\"info\":\"stand\"}");
}
void setSit() {
    servos_enabled = true;
    for (int i = 0; i < NUM_SERVOS; i++) {
        if (!servos[i].attached()) servos[i].attach(SERVO_PINS[i]);
        servo_targets[i] = SERVO_SIT[i];
    }
    Serial.println("{\"info\":\"sit\"}");
}
void stopServos() {
    servos_enabled = false;
    for (int i = 0; i < NUM_SERVOS; i++) {
        servos[i].detach();
        pinMode(SERVO_PINS[i], OUTPUT);
        digitalWrite(SERVO_PINS[i], LOW);
    }
    Serial.println("{\"info\":\"servos_stopped\"}");
}
void applyServos() {
    if (!servos_enabled) return;
    for (int i = 0; i < NUM_SERVOS; i++) {
        if (servos[i].attached()) {
            float diff = servo_targets[i] - servo_current[i];
            if (diff <= SERVO_SPEED && diff >= -SERVO_SPEED) {
                servo_current[i] = servo_targets[i];
            } else {
                servo_current[i] += (diff > 0.0f ? SERVO_SPEED : -SERVO_SPEED);
            }
            servos[i].write((int)servo_current[i]);
        }
    }
}

// ============================================================
// HC-SR04
// ============================================================
float readSonar() {
    digitalWrite(SONAR_TRIG_PIN, LOW);  delayMicroseconds(2);
    digitalWrite(SONAR_TRIG_PIN, HIGH); delayMicroseconds(10);
    digitalWrite(SONAR_TRIG_PIN, LOW);
    long dur = pulseIn(SONAR_ECHO_PIN, HIGH, 11600UL);
    if (dur == 0) { sonar_valid = false; return -1.0f; }
    float d = dur / 58.0f;
    if (d < SONAR_MIN_CM || d > SONAR_MAX_CM) { sonar_valid = false; return -1.0f; }
    sonar_history[sonar_idx] = d;
    sonar_idx = (sonar_idx + 1) % SONAR_SAMPLES;
    sonar_valid = true;
    float sum = 0;
    for (int i = 0; i < SONAR_SAMPLES; i++) sum += sonar_history[i];
    return sum / SONAR_SAMPLES;
}

// ============================================================
// Publication JSON (BNO085 + Sonar)
// ============================================================
void publishAll(float dist_cm) {
    bool alert = sonar_valid && (dist_cm > 0) && (dist_cm < SONAR_ALERT_CM);

    Serial.print("{\"imu\":{");
    Serial.print("\"qw\":"); Serial.print((int16_t)(bno_data.qw * 10000));
    Serial.print(",\"qx\":"); Serial.print((int16_t)(bno_data.qx * 10000));
    Serial.print(",\"qy\":"); Serial.print((int16_t)(bno_data.qy * 10000));
    Serial.print(",\"qz\":"); Serial.print((int16_t)(bno_data.qz * 10000));
    Serial.print(",\"lax\":"); Serial.print((int16_t)(bno_data.lax * 100));
    Serial.print(",\"lay\":"); Serial.print((int16_t)(bno_data.lay * 100));
    Serial.print(",\"laz\":"); Serial.print((int16_t)(bno_data.laz * 100));
    Serial.print(",\"gx\":"); Serial.print((int16_t)(bno_data.gx * 1000));
    Serial.print(",\"gy\":"); Serial.print((int16_t)(bno_data.gy * 1000));
    Serial.print(",\"gz\":"); Serial.print((int16_t)(bno_data.gz * 1000));
    Serial.print(",\"calib\":"); Serial.print(bno_data.calib);
    Serial.print("},\"sonar\":{");
    Serial.print("\"dist_cm\":"); Serial.print(dist_cm, 1);
    Serial.print(",\"valid\":"); Serial.print(sonar_valid ? "true" : "false");
    Serial.print(",\"alert\":"); Serial.print(alert ? "true" : "false");
    Serial.print("},\"version\":\"");
    Serial.print(SKETCH_VERSION);
    Serial.println("\"}");
}
