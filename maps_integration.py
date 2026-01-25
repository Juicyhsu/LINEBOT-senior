"""
Google Maps API 整合模組
提供地點搜尋、路線規劃、距離計算等功能
"""
import os
import requests
from typing import List, Dict, Optional, Tuple
from datetime import datetime

# Google Maps API Key
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")

class MapsIntegration:
    """Google Maps API 整合類別"""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or GOOGLE_MAPS_API_KEY
        self.base_url = "https://maps.googleapis.com/maps/api"
    
    def geocode(self, address: str, language: str = "zh-TW") -> Optional[Dict]:
        """
        地址轉經緯度（Geocoding）
        
        Args:
            address: 地址或地點名稱
            language: 語言代碼
            
        Returns:
            包含經緯度和格式化地址的字典，失敗返回 None
        """
        try:
            url = f"{self.base_url}/geocode/json"
            params = {
                "address": address,
                "key": self.api_key,
                "language": language
            }
            
            response = requests.get(url, params=params)
            data = response.json()
            
            if data["status"] == "OK" and len(data["results"]) > 0:
                result = data["results"][0]
                return {
                    "lat": result["geometry"]["location"]["lat"],
                    "lng": result["geometry"]["location"]["lng"],
                    "formatted_address": result["formatted_address"],
                    "place_id": result.get("place_id")
                }
            else:
                print(f"Geocoding failed: {data.get('status')}")
                return None
        except Exception as e:
            print(f"Geocoding error: {e}")
            return None
    
    def reverse_geocode(self, lat: float, lng: float, language: str = "zh-TW") -> Optional[str]:
        """
        經緯度轉地址（Reverse Geocoding）
        
        Args:
            lat: 緯度
            lng: 經度
            language: 語言代碼
            
        Returns:
            格式化的地址字串，失敗返回 None
        """
        try:
            url = f"{self.base_url}/geocode/json"
            params = {
                "latlng": f"{lat},{lng}",
                "key": self.api_key,
                "language": language
            }
            
            response = requests.get(url, params=params)
            data = response.json()
            
            if data["status"] == "OK" and len(data["results"]) > 0:
                return data["results"][0]["formatted_address"]
            return None
        except Exception as e:
            print(f"Reverse geocoding error: {e}")
            return None
    
    def search_nearby_places(self, location: str, place_type: str = "tourist_attraction",
                            radius: int = 5000, language: str = "zh-TW") -> List[Dict]:
        """
        搜尋附近地點
        
        Args:
            location: 地點名稱或地址
            place_type: 地點類型（tourist_attraction, restaurant, hospital 等）
            radius: 搜尋半徑（公尺）
            language: 語言代碼
            
        Returns:
            地點清單
        """
        try:
            # 先取得經緯度
            geocode_result = self.geocode(location, language)
            if not geocode_result:
                return []
            
            lat = geocode_result["lat"]
            lng = geocode_result["lng"]
            
            url = f"{self.base_url}/place/nearbysearch/json"
            params = {
                "location": f"{lat},{lng}",
                "radius": radius,
                "type": place_type,
                "key": self.api_key,
                "language": language
            }
            
            response = requests.get(url, params=params)
            data = response.json()
            
            if data["status"] == "OK":
                places = []
                for place in data["results"][:10]:  # 限制回傳 10 個
                    places.append({
                        "name": place.get("name"),
                        "address": place.get("vicinity"),
                        "rating": place.get("rating"),
                        "user_ratings_total": place.get("user_ratings_total"),
                        "place_id": place.get("place_id"),
                        "types": place.get("types", [])
                    })
                return places
            return []
        except Exception as e:
            print(f"Search nearby places error: {e}")
            return []
    
    def get_directions(self, origin: str, destination: str, 
                      mode: str = "transit", language: str = "zh-TW") -> Optional[Dict]:
        """
        取得路線規劃
        
        Args:
            origin: 起點
            destination: 終點
            mode: 交通方式（driving, walking, bicycling, transit）
            language: 語言代碼
            
        Returns:
            路線資訊字典
        """
        try:
            url = f"{self.base_url}/directions/json"
            params = {
                "origin": origin,
                "destination": destination,
                "mode": mode,
                "key": self.api_key,
                "language": language,
                "region": "TW"
            }
            
            response = requests.get(url, params=params)
            data = response.json()
            
            if data["status"] == "OK" and len(data["routes"]) > 0:
                route = data["routes"][0]
                leg = route["legs"][0]
                
                return {
                    "distance": leg["distance"]["text"],
                    "distance_value": leg["distance"]["value"],  # 公尺
                    "duration": leg["duration"]["text"],
                    "duration_value": leg["duration"]["value"],  # 秒
                    "start_address": leg["start_address"],
                    "end_address": leg["end_address"],
                    "steps": self._parse_steps(leg["steps"])
                }
            else:
                print(f"Directions failed: {data.get('status')}")
                return None
        except Exception as e:
            print(f"Get directions error: {e}")
            return None
    
    def _parse_steps(self, steps: List[Dict]) -> List[Dict]:
        """解析路線步驟"""
        parsed_steps = []
        for step in steps:
            parsed_steps.append({
                "instruction": step["html_instructions"].replace("<b>", "").replace("</b>", "")
                                                         .replace("<div", "\n<div"),
                "distance": step["distance"]["text"],
                "duration": step["duration"]["text"],
                "travel_mode": step.get("travel_mode")
            })
        return parsed_steps
    
    def calculate_travel_time(self, origin: str, destination: str,
                             mode: str = "transit", 
                             departure_time: Optional[datetime] = None) -> Optional[Tuple[str, int]]:
        """
        計算旅行時間
        
        Args:
            origin: 起點
            destination: 終點
            mode: 交通方式
            departure_time: 出發時間（用於計算交通狀況）
            
        Returns:
            (時間文字, 時間秒數) 或 None
        """
        directions = self.get_directions(origin, destination, mode)
        if directions:
            return (directions["duration"], directions["duration_value"])
        return None
    
    def get_place_details(self, place_id: str, language: str = "zh-TW") -> Optional[Dict]:
        """
        取得地點詳細資訊
        
        Args:
            place_id: 地點 ID
            language: 語言代碼
            
        Returns:
            地點詳細資訊
        """
        try:
            url = f"{self.base_url}/place/details/json"
            params = {
                "place_id": place_id,
                "key": self.api_key,
                "language": language,
                "fields": "name,formatted_address,rating,opening_hours,wheelchair_accessible_entrance,formatted_phone_number,website"
            }
            
            response = requests.get(url, params=params)
            data = response.json()
            
            if data["status"] == "OK":
                result = data["result"]
                return {
                    "name": result.get("name"),
                    "address": result.get("formatted_address"),
                    "rating": result.get("rating"),
                    "phone": result.get("formatted_phone_number"),
                    "website": result.get("website"),
                    "wheelchair_accessible": result.get("wheelchair_accessible_entrance"),
                    "opening_hours": result.get("opening_hours", {}).get("weekday_text", [])
                }
            return None
        except Exception as e:
            print(f"Get place details error: {e}")
            return None
    
    def suggest_itinerary(self, location: str, interests: List[str], 
                         duration_hours: int = 8) -> List[Dict]:
        """
        AI 輔助行程建議（基於附近景點）
        
        Args:
            location: 起始地點
            interests: 興趣類型清單（tourist_attraction, restaurant, park 等）
            duration_hours: 行程時數
            
        Returns:
            建議的景點清單
        """
        all_places = []
        
        for interest in interests:
            places = self.search_nearby_places(location, interest)
            all_places.extend(places)
        
        # 簡單排序：根據評分和評論數
        sorted_places = sorted(
            all_places,
            key=lambda x: (x.get("rating", 0) * 0.7 + 
                          min(x.get("user_ratings_total", 0) / 1000, 5) * 0.3),
            reverse=True
        )
        
        # 根據時數選擇景點（假設每個景點 1-2 小時）
        num_places = min(duration_hours // 2, len(sorted_places))
        return sorted_places[:num_places]

# 全域 Maps 實例
maps = MapsIntegration()
