import re

def group_posts_by_series(posts, title_key='title'):
    """
    Groups posts identifying series parts (e.g. Name.part01.mp4).
    """
    grouped_posts = []
    series_map = {}
    
    for post in posts:
        # Regex to detect part files: Name.part01.mp4 or Name part 1.mkv
        title = post.get(title_key, '')
        match = re.search(r'(.*)[ ._]part(\d+)', title, re.IGNORECASE)
        
        if match:
            series_name = match.group(1).strip()
            part_number = int(match.group(2))
            
            if series_name not in series_map:
                series_map[series_name] = []
            
            # Store part with its number
            post['part_number'] = part_number
            series_map[series_name].append(post)
        else:
            grouped_posts.append(post)
            
    # Process Grouped Series
    for series_name, parts in series_map.items():
        # Sort by part number
        parts.sort(key=lambda x: x.get('part_number', 0))
        
        # Take the first part as representative
        if parts:
            representative = parts[0]
            representative['is_series'] = True
            representative['parts_count'] = len(parts)
            # Ensure the representative has the series title (optional, but good for display)
            # But we shouldn't overwrite original title too destructively if needed specific file
            # However, for listing, showing series name is usually expected.
            representative[title_key] = series_name 
            grouped_posts.append(representative)
    
    return grouped_posts
