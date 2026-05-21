#include "monocular-slam-node.hpp"

#include <opencv2/core/core.hpp>
#include <opencv2/imgproc/imgproc.hpp>
#include <sensor_msgs/point_cloud2_iterator.hpp>
#include <tf2/LinearMath/Quaternion.h>
#include <tf2/LinearMath/Transform.h>
#include <geometry_msgs/msg/transform_stamped.hpp>

using std::placeholders::_1;

MonocularSlamNode::MonocularSlamNode(ORB_SLAM3::System* pSLAM)
:   Node("ORB_SLAM3_ROS2")
{
    m_SLAM = pSLAM;

    m_image_subscriber = this->create_subscription<sensor_msgs::msg::Image>(
        "camera",
        10,
        std::bind(&MonocularSlamNode::GrabImage, this, std::placeholders::_1));
        
    m_map_points_pub = this->create_publisher<sensor_msgs::msg::PointCloud2>("orb_slam3/map_points", 10);
    m_pose_pub = this->create_publisher<geometry_msgs::msg::PoseStamped>("orb_slam3/camera_pose", 10);
    m_path_pub = this->create_publisher<nav_msgs::msg::Path>("orb_slam3/camera_path", 10);
    m_tracked_image_pub = this->create_publisher<sensor_msgs::msg::Image>("orb_slam3/tracking_image", 10);
    
    m_tf_broadcaster = std::make_shared<tf2_ros::TransformBroadcaster>(this);
    
    m_path.header.frame_id = "map";
    
    RCLCPP_INFO(this->get_logger(), "ORB_SLAM3 Monocular Node initialized.");
}

MonocularSlamNode::~MonocularSlamNode()
{
    m_SLAM->Shutdown();
    m_SLAM->SaveKeyFrameTrajectoryTUM("KeyFrameTrajectory.txt");
}

void MonocularSlamNode::GrabImage(const sensor_msgs::msg::Image::SharedPtr msg)
{
    try
    {
        m_cvImPtr = cv_bridge::toCvCopy(msg);
    }
    catch (cv_bridge::Exception& e)
    {
        RCLCPP_ERROR(this->get_logger(), "cv_bridge exception: %s", e.what());
        return;
    }

    // Process frame
    Sophus::SE3f Tcw = m_SLAM->TrackMonocular(m_cvImPtr->image, msg->header.stamp.sec + msg->header.stamp.nanosec * 1e-9);
    
    // Draw Keypoints
    std::vector<cv::KeyPoint> vKPs = m_SLAM->GetTrackedKeyPointsUn();
    for (const auto& kp : vKPs)
    {
        cv::circle(m_cvImPtr->image, kp.pt, 2, cv::Scalar(0, 255, 0), -1);
    }
    m_tracked_image_pub->publish(*m_cvImPtr->toImageMsg());

    // Publish TF and Pose if tracking is successful
    if(!Tcw.translation().hasNaN())
    {
        Sophus::SE3f Twc = Tcw.inverse();
        
        Eigen::Vector3f t = Twc.translation();
        Eigen::Quaternionf q = Twc.unit_quaternion();
        
        geometry_msgs::msg::TransformStamped t_msg;
        t_msg.header.stamp = msg->header.stamp;
        t_msg.header.frame_id = "map";
        t_msg.child_frame_id = "camera";
        
        t_msg.transform.translation.x = t.x();
        t_msg.transform.translation.y = t.y();
        t_msg.transform.translation.z = t.z();
        
        t_msg.transform.rotation.x = q.x();
        t_msg.transform.rotation.y = q.y();
        t_msg.transform.rotation.z = q.z();
        t_msg.transform.rotation.w = q.w();
        
        m_tf_broadcaster->sendTransform(t_msg);
        
        geometry_msgs::msg::PoseStamped pose_msg;
        pose_msg.header = t_msg.header;
        pose_msg.pose.position.x = t.x();
        pose_msg.pose.position.y = t.y();
        pose_msg.pose.position.z = t.z();
        pose_msg.pose.orientation = t_msg.transform.rotation;
        
        m_pose_pub->publish(pose_msg);
        
        m_path.header.stamp = msg->header.stamp;
        m_path.poses.push_back(pose_msg);
        m_path_pub->publish(m_path);
    }

    // Publish Map Points
    std::vector<ORB_SLAM3::MapPoint*> vpMPs = m_SLAM->GetTrackedMapPoints();
    if(vpMPs.empty()) return;
    
    sensor_msgs::msg::PointCloud2 pc_msg;
    pc_msg.header.stamp = msg->header.stamp;
    pc_msg.header.frame_id = "map";
    pc_msg.height = 1;
    pc_msg.width = vpMPs.size();
    pc_msg.is_dense = true;
    pc_msg.is_bigendian = false;
    
    sensor_msgs::PointCloud2Modifier modifier(pc_msg);
    modifier.setPointCloud2FieldsByString(1, "xyz");
    modifier.resize(vpMPs.size());
    
    sensor_msgs::PointCloud2Iterator<float> iter_x(pc_msg, "x");
    sensor_msgs::PointCloud2Iterator<float> iter_y(pc_msg, "y");
    sensor_msgs::PointCloud2Iterator<float> iter_z(pc_msg, "z");
    
    int points_added = 0;
    for(size_t i = 0; i < vpMPs.size(); ++i)
    {
        if(vpMPs[i] && !vpMPs[i]->isBad())
        {
            Eigen::Vector3f pos = vpMPs[i]->GetWorldPos();
            *iter_x = pos.x();
            *iter_y = pos.y();
            *iter_z = pos.z();
            ++iter_x; ++iter_y; ++iter_z;
            points_added++;
        }
    }
    
    if (points_added > 0)
    {
        modifier.resize(points_added);
        m_map_points_pub->publish(pc_msg);
    }
}
