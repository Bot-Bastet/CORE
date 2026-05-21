import time
import board
import busio
import numpy as np
import math

try:
    from adafruit_bno08x import BNO_REPORT_ACCELEROMETER, BNO_REPORT_GYROSCOPE, BNO_REPORT_ROTATION_VECTOR
    from adafruit_bno08x.i2c import BNO08X_I2C
    BNO_AVAILABLE = True
except ImportError:
    BNO_AVAILABLE = False
    print("WARNING: adafruit-circuitpython-bno08x library not installed. Please run:")
    print("pip install adafruit-circuitpython-bno08x")

class IMU:
    def __init__(self, rp_flip=True, r_neg=False, p_neg=True, y_neg=True):
        # IMU Parameters: gyro(x,y,z), acc(x,y,z), mag(x,y,z)
        self.imu_data = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        self.roll = 0.0
        self.pitch = 0.0
        self.yaw = 0.0
        self.true_roll = 0.0
        self.true_pitch = 0.0

        # Used to turn the IMU into right-hand coordinate system
        self.rp_flip = rp_flip
        self.r_neg = r_neg
        self.p_neg = p_neg
        self.y_neg = y_neg

        if BNO_AVAILABLE:
            try:
                # Primary I2C bus on Raspberry Pi 5 (Pins 3 & 5)
                self.i2c = busio.I2C(board.SCL, board.SDA)
                self.sensor = BNO08X_I2C(self.i2c)
                
                # Enable sensor reports
                self.sensor.enable_feature(BNO_REPORT_ACCELEROMETER)
                self.sensor.enable_feature(BNO_REPORT_GYROSCOPE)
                self.sensor.enable_feature(BNO_REPORT_ROTATION_VECTOR)
                print("BNO08x Sensor Connected and Initialized Successfully!")
            except Exception as e:
                print(f"Error connecting to BNO08x over I2C: {e}")
                self.sensor = None
        else:
            self.sensor = None
            print("Running in Mock IMU mode (BNO08x library missing)")

    def filter_rpy(self):
        """
        Reads fused quaternions directly from BNO08x and converts them to Roll, Pitch, and Yaw in degrees.
        """
        if self.sensor is None:
            return

        try:
            # 1. Read Quaternion from sensor-side fusion
            quat_x, quat_y, quat_z, quat_w = self.sensor.quaternion
            
            # 2. Convert Quaternion to Euler angles (radians -> degrees)
            # Roll (X-axis rotation)
            sinr_cosp = 2.0 * (quat_w * quat_x + quat_y * quat_z)
            cosr_cosp = 1.0 - 2.0 * (quat_x * quat_x + quat_y * quat_y)
            self.roll = math.atan2(sinr_cosp, cosr_cosp) * 180.0 / np.pi

            # Pitch (Y-axis rotation)
            sinp = 2.0 * (quat_w * quat_y - quat_z * quat_x)
            if abs(sinp) >= 1.0:
                self.pitch = math.copysign(math.pi / 2.0, sinp) * 180.0 / np.pi
            else:
                self.pitch = math.asin(sinp) * 180.0 / np.pi

            # Yaw (Z-axis rotation)
            siny_cosp = 2.0 * (quat_w * quat_z + quat_x * quat_y)
            cosy_cosp = 1.0 - 2.0 * (quat_y * quat_y + quat_z * quat_z)
            self.yaw = math.atan2(siny_cosp, cosy_cosp) * 180.0 / np.pi

            # 3. Read Accel & Gyro values
            accel_x, accel_y, accel_z = self.sensor.acceleration
            gyro_x, gyro_y, gyro_z = self.sensor.gyro

            # 4. Populate shared state structure for ROS node compatibility
            self.imu_data[0] = gyro_x
            self.imu_data[1] = gyro_y
            self.imu_data[2] = gyro_z
            self.imu_data[3] = accel_x
            self.imu_data[4] = accel_y
            self.imu_data[5] = accel_z

            # 5. Apply frame transformations
            self.recenter_rp()

        except Exception as e:
            # Prevent logging spam if a single transfer fails
            pass

    def recenter_rp(self):
        """ Adjusts coordinate frame sign conventions to match right-hand coordinate standards. """
        if self.rp_flip:
            self.true_roll = -self.pitch if self.r_neg else self.pitch
            self.true_pitch = -self.roll if self.p_neg else self.roll
        else:
            self.true_roll = -self.roll if self.r_neg else self.roll
            self.true_pitch = -self.pitch if self.p_neg else self.pitch

        if self.y_neg:
            self.yaw = -self.yaw

    # Dummy methods to prevent any AttributeError in legacy calibration loops
    def calibrate_imu(self):
        print("Note: BNO08x uses automatic self-calibration. No manual calibration needed.")
        return True

    def load_magnemometer_calibration(self):
        return True

    def calibrate_magnemometer(self):
        print("Note: BNO08x handles magnetometer calibration internally.")
        return True


if __name__ == "__main__":
    imu = IMU()
    print("Testing BNO08x loop. Press Ctrl+C to stop.")
    try:
        while True:
            imu.filter_rpy()
            print(f"Roll: {imu.true_roll:.2f} | Pitch: {imu.true_pitch:.2f} | Yaw: {imu.yaw:.2f} | "
                  f"Accel: ({imu.imu_data[3]:.2f}, {imu.imu_data[4]:.2f}, {imu.imu_data[5]:.2f})")
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nTest finished.")