-- Atomic token bucket refill + consume.
-- See ARCHITECTURE.md §5. Runs as a single Redis EVALSHA round trip so
-- concurrent requests from the same tenant can't both observe "1 token
-- left" and both proceed (the read-then-write race a naive GET/SET
-- implementation would have).
--
-- KEYS[1] = bucket hash key, e.g. "ratelimit:{tenant_id}:{model}"
-- ARGV[1] = capacity (burst)
-- ARGV[2] = refill_rate_per_second (requests_per_minute / 60)
-- ARGV[3] = now (unix timestamp, seconds, float)
-- ARGV[4] = tokens requested (normally 1)
--
-- Returns: {allowed (0/1), tokens_remaining, retry_after_milliseconds}
--
-- retry_after is returned in MILLISECONDS, as an integer, deliberately.
-- Redis converts a Lua script's float replies to Redis integer replies by
-- truncating toward zero (this is real-Redis behavior, not a fakeredis
-- quirk) — a fractional "wait 0.9s" would silently come back as "0" if
-- returned in seconds, which would make a caller busy-retry instead of
-- backing off. Returning whole milliseconds avoids losing that precision;
-- the Python caller (gateway/ratelimit/token_bucket.py) divides by 1000.

local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])

local bucket = redis.call("HMGET", key, "tokens", "last_refill_ts")
local tokens = tonumber(bucket[1])
local last_refill_ts = tonumber(bucket[2])

if tokens == nil then
  tokens = capacity
  last_refill_ts = now
end

local elapsed = math.max(0, now - last_refill_ts)
tokens = math.min(capacity, tokens + elapsed * refill_rate)

local allowed = 0
local retry_after_ms = 0

if tokens >= requested then
  tokens = tokens - requested
  allowed = 1
else
  local deficit = requested - tokens
  retry_after_ms = math.ceil((deficit / refill_rate) * 1000)
end

redis.call("HMSET", key, "tokens", tokens, "last_refill_ts", now)
redis.call("EXPIRE", key, 3600)

return {allowed, tokens, retry_after_ms}
