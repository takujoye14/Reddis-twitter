from redis import Redis
from typing import Union
from fastapi import FastAPI, Response, status, Query
from pydantic import BaseModel
import bcrypt
import time

redis = Redis(host="redis", port=6379, decode_responses=True)


class NewUser(BaseModel):
    username: str
    password: Union[str, None] = None


class User(BaseModel):
    id: int
    username: str
    follower_count: int
    following_count: int
    following: list[int]
    followers: list[int]


class NewPost(BaseModel):
    user_id: int
    content: str


class LoginData(BaseModel):
    user_id: int
    password: str


tags_metadata = [
    {"name": "Users", "description": "Operations with users."},
    {"name": "Posts", "description": "Create and fetch posts."}
]

app = FastAPI(openapi_tags=tags_metadata)

# API Endpoints

## Users

### Create User
@app.post("/user/", tags=["Users"])
async def create_user(user: NewUser):
    user_id = redis.incr("seq:user")
    hashed_password = get_hashed_password(user.password.encode())
    user_info = {
        "id": user_id,
        "username": user.username,
        "password": hashed_password,
        "follower_count": 0,
        "following_count": 0,
    }
    redis.hmset(f"user:{user_id}", user_info)
    redis.set(f"username:{user.username}", user_id)  # 🔍 username lookup
    return {"success": True, "user_id": user_id}


### Get User By Username
@app.get("/user/by-username", tags=["Users"])
async def get_user_by_username(username: str, response: Response):
    user_id = redis.get(f"username:{username}")
    if not user_id:
        response.status_code = status.HTTP_404_NOT_FOUND
        return {"success": False, "message": "Username not found"}

    user_info = redis.hmget(
        f"user:{user_id}", ["username", "following_count", "follower_count"]
    )
    return User(
        id=int(user_id),
        username=user_info[0],
        following_count=int(user_info[1]),
        follower_count=int(user_info[2]),
        following=redis.zrange(f"following:{user_id}", 0, -1),
        followers=redis.zrange(f"followers:{user_id}", 0, -1),
    )


### Follow a User
@app.post("/user/follow", tags=["Users"])
async def follow_user(follower_id: int, followed_id: int):
    timestamp = int(time.time())
    if not redis.hgetall(f"user:{follower_id}"):
        return {"success": False, "message": "Follower user does not exist"}
    if not redis.hgetall(f"user:{followed_id}"):
        return {"success": False, "message": "Followed user does not exist"}
    if redis.zadd(f"followers:{followed_id}", {follower_id: timestamp}) == 0:
        return {"success": False, "message": "User is already followed"}
    redis.zadd(f"following:{follower_id}", {followed_id: timestamp})
    redis.hincrby(f"user:{follower_id}", "following_count", 1)
    redis.hincrby(f"user:{followed_id}", "follower_count", 1)
    return {"success": True}


### Unfollow a User
@app.post("/user/unfollow", tags=["Users"])
async def unfollow_user(follower_id: int, followed_id: int):
    if not redis.exists(f"user:{follower_id}") or not redis.exists(f"user:{followed_id}"):
        return {"success": False, "message": "One or both users do not exist"}

    removed1 = redis.zrem(f"followers:{followed_id}", follower_id)
    removed2 = redis.zrem(f"following:{follower_id}", followed_id)

    if removed1 == 0 or removed2 == 0:
        return {"success": False, "message": "User was not followed"}

    redis.hincrby(f"user:{follower_id}", "following_count", -1)
    redis.hincrby(f"user:{followed_id}", "follower_count", -1)
    return {"success": True, "message": "Successfully unfollowed"}


### Get User By ID
@app.get("/user/{id}", tags=["Users"])
async def get_user(id: int, response: Response) -> User:
    if redis.exists(f"user:{id}") == 0:
        response.status_code = status.HTTP_404_NOT_FOUND
        return

    user_info = redis.hmget(
        f"user:{id}", ["username", "following_count", "follower_count"]
    )
    return User(
        id=id,
        username=user_info[0],
        following_count=int(user_info[1]),
        follower_count=int(user_info[2]),
        following=redis.zrange(f"following:{id}", 0, -1),
        followers=redis.zrange(f"followers:{id}", 0, -1),
    )


### Authenticate a User
@app.post("/user/authenticate", tags=["Users"])
async def authenticate_user(data: LoginData):
    if not redis.exists(f"user:{data.user_id}"):
        return {"success": False, "message": "User does not exist"}

    stored_hash = redis.hget(f"user:{data.user_id}", "password")
    if not stored_hash:
        return {"success": False, "message": "Password not set"}

    if check_password(data.password.encode(), stored_hash.encode()):
        return {"success": True, "message": "Authentication successful"}
    else:
        return {"success": False, "message": "Incorrect password"}


### Get User Followers (with pagination)
@app.get("/users/{user_id}/followers", tags=["Users"])
async def get_user_followers(user_id: int, start: int = Query(0), stop: int = Query(10)):
    key = f"followers:{user_id}"

    if not redis.exists(f"user:{user_id}"):
        return {"success": False, "message": "User does not exist"}

    followers = redis.zrange(key, start, stop)
    return {
        "user_id": user_id,
        "start": start,
        "stop": stop,
        "count": len(followers),
        "followers": followers,
    }


### Get User Following (with pagination)
@app.get("/users/{user_id}/following", tags=["Users"])
async def get_user_following(user_id: int, start: int = Query(0), stop: int = Query(10)):
    key = f"following:{user_id}"

    if not redis.exists(f"user:{user_id}"):
        return {"success": False, "message": "User does not exist"}

    following = redis.zrange(key, start, stop)
    return {
        "user_id": user_id,
        "start": start,
        "stop": stop,
        "count": len(following),
        "following": following,
    }


## Posts

### Create a Post
@app.post("/post/", tags=["Posts"])
async def create_post(post: NewPost):
    if not redis.exists(f"user:{post.user_id}"):
        return {"success": False, "message": "User does not exist"}

    post_id = redis.incr("post:id")
    timestamp = int(time.time())

    post_data = {
        "id": post_id,
        "user_id": post.user_id,
        "content": post.content,
        "timestamp": timestamp
    }

    redis.hmset(f"post:{post_id}", post_data)
    redis.lpush(f"posts:{post.user_id}", post_id)

    return {"success": True, "post_id": post_id}


### Get User Posts (with pagination)
@app.get("/users/{user_id}/posts", tags=["Posts"])
async def get_user_posts(user_id: int, start: int = Query(0), stop: int = Query(10)):
    if not redis.exists(f"user:{user_id}"):
        return {"success": False, "message": "User does not exist"}

    post_ids = redis.lrange(f"posts:{user_id}", start, stop)
    posts = []

    for post_id in post_ids:
        post_data = redis.hgetall(f"post:{post_id}")
        if post_data:
            posts.append(post_data)

    return {
        "user_id": user_id,
        "start": start,
        "stop": stop,
        "count": len(posts),
        "posts": posts
    }


# Helper functions
def get_hashed_password(plain_text_password):
    return bcrypt.hashpw(plain_text_password, bcrypt.gensalt())


def check_password(plain_text_password, hashed_password):
    return bcrypt.checkpw(plain_text_password, hashed_password)
