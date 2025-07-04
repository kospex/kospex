# GitHub Rate Limits Reference

## Overview

GitHub applies different rate limiting strategies for different types of operations. This document provides a comprehensive overview of rate limits for API calls, Git operations, and related services.

## REST API Rate Limits

### Standard Rate Limits

| Authentication Type | Requests per Hour | Notes |
|-------------------|------------------|-------|
| Unauthenticated | 60 | Shared across all unauthenticated requests from same IP |
| Personal Access Token | 5,000 | Per token |
| OAuth App | 5,000 | Per token |
| GitHub App | 5,000-15,000 | Varies by organization type |
| GitHub Enterprise Cloud | 15,000 | Per user |

### Secondary Rate Limits

- **Concurrent Requests**: 100 maximum concurrent requests
- **Content Creation**: 80 requests per hour for content-creating endpoints
- **Search API**: 30 requests per minute (authenticated), 10 requests per minute (unauthenticated)

### API-Specific Limits

#### Git Trees API
- **Maximum entries**: 100,000 files/directories per request
- **Maximum response size**: 7 MB
- **Truncation**: Returns `"truncated": true` when limits exceeded
- **No pagination**: Must manually traverse subdirectories for large repos
- **No timestamps**: Does not return last modified dates

#### Repository Contents API
- **Rate**: Subject to standard API limits
- **Efficiency**: 1 API call per directory traversed
- **Metadata**: Includes `last_modified` timestamps
- **Best for**: Small to medium repositories with timestamp requirements

## Git Protocol Rate Limits (Clone/Push/Pull)

### Unauthenticated Operations (Public Repos)

- **Hard Limits**: None - operates under fair use policy
- **Throttling**: Dynamic based on server load and resource usage
- **Typical Speed**: ~500 KB/s via HTTPS
- **Concurrent Connections**: Limited per IP/user/repository to prevent DoS

### Authenticated Operations

- **Authentication Required**: Since August 2021 (SSH keys or Personal Access Tokens)
- **Performance**: Significantly better than unauthenticated
- **SSH Speed**: Can exceed 5 MB/s (variable)
- **HTTPS Speed**: Similar to unauthenticated (~500 KB/s)
- **Throttling**: CPU-based rather than bandwidth-based

### Key Differences from API Limits

| Aspect | REST API | Git Operations |
|--------|----------|----------------|
| Rate Limiting | Strict numerical limits | Fair use + dynamic throttling |
| Quota Consumption | Counts against API quota | Separate from API quota |
| Throttling Method | Requests per time period | Concurrent connections + CPU |
| Authentication Impact | Higher request limits | Better performance |

## Platform Differences

### GitHub.com
- **API**: Standard rate limits as listed above
- **Git**: Fair use policy with dynamic throttling
- **No Admin Controls**: Users cannot configure custom limits

### GitHub Enterprise Server
- **API**: Similar to GitHub.com with potential customization
- **Git**: Configurable rate limits through Management Console
- **Admin Controls**: Granular limits per repository/user
- **Flexibility**: Limits can be adjusted based on server capacity

## Best Practices

### For API Usage
1. **Always authenticate** for higher rate limits
2. **Use conditional requests** with ETag headers (don't count if 304 response)
3. **Implement exponential backoff** when rate limited
4. **Cache responses** where possible
5. **Use webhooks** instead of polling for real-time updates

### For Git Operations
1. **Use SSH keys** for better performance
2. **Avoid excessive concurrent clones** of the same repository
3. **Batch operations** where possible
4. **Monitor usage patterns** to stay within fair use
5. **Consider local caching** for frequently accessed repositories

### For Large Repositories
1. **Check for truncation** in Git Trees API responses
2. **Implement manual tree traversal** for repos >100K files
3. **Consider shallow clones** (`--depth=1`) for CI/CD
4. **Use Git LFS** for large binary files
5. **Evaluate local Git operations** vs API calls for bulk operations

## Rate Limit Headers

GitHub returns rate limit information in response headers:

```
X-RateLimit-Limit: 5000
X-RateLimit-Remaining: 4999
X-RateLimit-Reset: 1372700873
X-RateLimit-Used: 1
X-RateLimit-Resource: core
```

## Error Responses

### API Rate Limit Exceeded
```json
{
  "message": "API rate limit exceeded for user ID 12345.",
  "documentation_url": "https://docs.github.com/rest/overview/resources-in-the-rest-api#rate-limiting"
}
```

### Secondary Rate Limit
```json
{
  "message": "You have exceeded a secondary rate limit. Please wait a few minutes before you try again.",
  "documentation_url": "https://docs.github.com/rest/overview/resources-in-the-rest-api#secondary-rate-limits"
}
```

## Related Services

### Git LFS
- **Bandwidth**: 1 GiB/month free
- **Storage**: 1 GiB free
- **Paid Tiers**: Available for higher limits

### GitHub Actions
- **Minutes**: 2,000 minutes/month free (Linux)
- **Storage**: 500 MB free
- **Concurrent Jobs**: Varies by plan

### GitHub Packages
- **Storage**: 500 MB free
- **Transfer**: 1 GiB/month free
- **Rate Limits**: Subject to standard API limits

## Monitoring and Troubleshooting

### Checking Current Limits
```bash
curl -H "Authorization: token YOUR_TOKEN" https://api.github.com/rate_limit
```

### Common Issues
1. **Hitting API limits**: Implement proper authentication and caching
2. **Slow clones**: Use SSH instead of HTTPS, check for throttling
3. **Truncated responses**: Implement manual tree traversal
4. **Secondary limits**: Reduce request frequency, implement backoff

## Additional Resources

- [GitHub REST API Rate Limiting](https://docs.github.com/en/rest/overview/resources-in-the-rest-api#rate-limiting)
- [GitHub Git LFS](https://docs.github.com/en/repositories/working-with-files/managing-large-files)
- [GitHub Enterprise Server Rate Limiting](https://docs.github.com/en/enterprise-server/admin/configuration/configuring-rate-limits)

---

*Last updated: July 2025*