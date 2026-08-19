import cv2
import numpy as np

from .detector import auto_detect_court_corners, render_auto_court_preview


class CourtMapper:
    def __init__(self, image_court_corners, court_dimensions=(6.1, 13.4)):
        """
        Initialize CourtMapper with court corners and dimensions
        Args:
            image_court_corners: List of 4 points [(x1,y1), ...] representing court corners in image
            court_dimensions: Tuple of (width, height) in meters, default badminton court size
        """
        self.image_court_corners = np.array(image_court_corners, dtype=np.float32)
        self.court_dimensions = court_dimensions
        court_points = np.array([
            [0, 0], [court_dimensions[0], 0],
            [court_dimensions[0], court_dimensions[1]], [0, court_dimensions[1]]
        ], dtype=np.float32)
        self.matrix = cv2.getPerspectiveTransform(self.image_court_corners, court_points)
        self.inv_matrix = cv2.getPerspectiveTransform(court_points, self.image_court_corners)

        self.compute_court_overlay()

    def image_to_court(self, point):
        """
        Transform image coordinates to court coordinates (meters)
        Args:
            point: (x,y) coordinates in image space
        Returns:
            Transformed (x,y) coordinates in court space
        """
        if not isinstance(point, (list, tuple, np.ndarray)) or not point:
            return []
        point = np.array(point, dtype=np.float32).reshape(-1, 1, 2)
        transformed_points = cv2.perspectiveTransform(point, self.matrix)
        return np.round(transformed_points[0][0], 2)

    def court_to_image(self, points):
        """
        Transform court coordinates (meters) to image coordinates
        Args:
            points: (x,y) coordinates in court space
        Returns:
            Transformed (x,y) coordinates in image space
        """
        if not isinstance(points, (list, tuple, np.ndarray)) or len(np.array(points).flatten()) == 0:
            return []

        points = np.array(points, dtype=np.float32).reshape(-1, 1, 2)
        transformed_points = cv2.perspectiveTransform(points, self.inv_matrix)
        return np.round(transformed_points[0][0], 2)

    def compute_court_overlay(self):
        thirds = [2.033, 4.066]
        self.vertical_lines = []
        for x in thirds:
            top = np.array([x, 0])
            bottom = np.array([x, 13.4])
            top_image = self.court_to_image(top)
            bottom_image = self.court_to_image(bottom)
            self.vertical_lines.append((top_image, bottom_image))

        ninths = np.linspace(0, 13.4, 11)
        self.horizontal_lines = []
        for y in ninths:
            left = np.array([0, y])
            right = np.array([6.1, y])
            left_image = self.court_to_image(left)
            right_image = self.court_to_image(right)
            self.horizontal_lines.append((left_image, right_image))

        left_mid = np.array([0, 6.7])
        right_mid = np.array([6.1, 6.7])
        left_mid_image = self.court_to_image(left_mid)
        right_mid_image = self.court_to_image(right_mid)
        self.mid_height = int((left_mid_image[1] + right_mid_image[1]) / 2)

    def draw_court_overlay(self, image):
        overlay = image.copy()
        cv2.polylines(overlay, [self.image_court_corners.astype(int)], True, (0, 255, 0), 2)

        for line in self.vertical_lines:
            cv2.line(overlay, tuple(line[0].astype(int)), tuple(line[1].astype(int)), (0, 255, 0), 1)

        for line in self.horizontal_lines:
            cv2.line(overlay, tuple(line[0].astype(int)), tuple(line[1].astype(int)), (0, 255, 0), 1)

        return overlay, self.mid_height


def compute_expanded_roi(court_corners, image_shape):
    """
    Build a player-detection ROI from the four court corners.
    The ROI keeps horizontal padding modest and expands more vertically,
    then clamps to the image bounds.
    """
    height, width = image_shape[:2]
    points = np.array(court_corners, dtype=np.int32)
    min_x = int(np.min(points[:, 0]))
    max_x = int(np.max(points[:, 0]))
    min_y = int(np.min(points[:, 1]))
    max_y = int(np.max(points[:, 1]))

    court_width = max_x - min_x
    pad_x = max(12, int(court_width * 0.08))

    x1 = max(0, min_x - pad_x)
    y1 = 0
    x2 = min(width - 1, max_x + pad_x)
    y2 = height - 1
    return [(x1, y1), (x2, y2)]


def annotate_court(image, auto_preview_path=None):
    """
    Return automatically detected court corners and ROI.
    """
    if not isinstance(image, np.ndarray):
        print("Error: Invalid image input")
        return None, None, None

    original_height, original_width = image.shape[:2]
    fixed_size = (1080, 720)
    base_image = cv2.resize(image, fixed_size)

    auto_corners, _line_mask, auto_debug = auto_detect_court_corners(base_image)
    if not auto_corners:
        auto_corners = [
            (int(fixed_size[0] * 0.24), int(fixed_size[1] * 0.40)),
            (int(fixed_size[0] * 0.76), int(fixed_size[1] * 0.40)),
            (int(fixed_size[0] * 0.90), int(fixed_size[1] * 0.94)),
            (int(fixed_size[0] * 0.10), int(fixed_size[1] * 0.94)),
        ]
        if auto_preview_path:
            print(f"No reliable auto court boundary found. Using fallback court annotation: {auto_preview_path}")

    auto_roi_corners = compute_expanded_roi(auto_corners, base_image.shape)
    auto_preview = render_auto_court_preview(base_image, auto_corners, auto_roi_corners, auto_debug)
    if auto_preview_path:
        cv2.imwrite(auto_preview_path, auto_preview)

    court_mapper = CourtMapper(auto_corners)
    _, auto_mid_height = court_mapper.draw_court_overlay(base_image)
    scale_x = original_width / fixed_size[0]
    scale_y = original_height / fixed_size[1]
    original_corners = [(int(x * scale_x), int(y * scale_y)) for x, y in auto_corners]
    original_roi_corners = [(int(x * scale_x), int(y * scale_y)) for x, y in auto_roi_corners]
    return original_corners, original_roi_corners, int(auto_mid_height * scale_y)


if __name__ == "__main__":
    image_path = r'images/Weixin Screenshot_00001.png'
    corners = [(426, 385), (861, 382), (996, 667), (288, 668)]
    court_mapper = CourtMapper(corners)
    centroids = [(1400, 1000), (700, 600), (800, 980)]
    image = cv2.imread(image_path)
    image, mid = court_mapper.draw_court_overlay(image)
    cv2.imshow("image", image)
    cv2.waitKey()
    for centroid in centroids:
        mapped_positions = court_mapper.image_to_court(centroid)
