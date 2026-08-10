# FlowKit REST API 接口文档

> Base URL: `http://127.0.0.1:8100/api`
>
> 所有端点均挂在 `/api/flow/` 前缀下。响应格式均为 JSON。
> 当 Chrome 扩展未连接时，所有需要扩展代理的端点返回 `503 Extension not connected`。

---

## 目录

1. [系统状态](#1-系统状态)
2. [Google Flow 项目管理](#2-google-flow-项目管理)
3. [Google Flow 媒体查询](#3-google-flow-媒体查询)
4. [图片生成 & 编辑](#4-图片生成--编辑)
5. [视频生成 & 管理](#5-视频生成--管理)
6. [图片上传](#6-图片上传)
7. [媒体下载 & 代理](#7-媒体下载--代理)
8. [高级 / 调试](#8-高级--调试)
9. [前端集成指南](#9-前端集成指南)

---

## 1. 系统状态

### `GET /api/flow/status`

检查 Chrome 扩展连接状态。

**响应示例：**
```json
{
  "connected": true,
  "flow_key_present": true
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `connected` | bool | 扩展 WebSocket 是否已连接 |
| `flow_key_present` | bool | 是否已获取 Flow API 密钥 |

---

### `GET /api/flow/credits`

获取当前用户的 Google Flow 积分余额。

**响应示例：**
```json
{
  "credits": 1500,
  "tier": "PAYGATE_TIER_TWO"
}
```

---

## 2. Google Flow 项目管理

### `GET /api/flow/projects`

列出当前用户在 Google Flow 中的**所有项目**。

**响应示例：**
```json
{
  "projects": [
    {
      "projectId": "2bc7c7bc-eb18-4975-9deb-6f93c3df3afb",
      "projectInfo": {
        "projectTitle": "出图-01",
        "thumbnailMediaKey": "90f3a512-3a0a-47a8-b7b3-533acdedcec9"
      },
      "creationTime": "2026-08-09T17:22:18.640216Z",
      "agentInfo": {
        "defaultGenerationSettings": {
          "imageDefaults": {
            "aspectRatio": "IMAGE_ASPECT_RATIO_LANDSCAPE",
            "outputCount": 2,
            "imageModelFamilyKey": "narwhal_display"
          },
          "videoDefaults": {
            "videoModelFamilyKey": "abra",
            "aspectRatio": "VIDEO_ASPECT_RATIO_LANDSCAPE",
            "outputCount": 1
          }
        },
        "creditSpendApprovalPolicy": "AUTO_APPROVE",
        "agentToggleState": "AGENT_TOGGLE_STATE_DISABLED"
      }
    }
  ]
}
```

| 字段路径 | 类型 | 说明 |
|----------|------|------|
| `projects[].projectId` | string (UUID) | 项目唯一 ID |
| `projects[].projectInfo.projectTitle` | string | 项目标题 |
| `projects[].projectInfo.thumbnailMediaKey` | string (UUID) | 缩略图的 mediaKey，可用于构建图片 URL |
| `projects[].creationTime` | string (ISO 8601) | 创建时间 |
| `projects[].agentInfo` | object \| null | 项目生成配置（可选） |

> **缩略图 URL 构建方式：**
> `https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={thumbnailMediaKey}`
> 该 URL 会 302 重定向至签名的 CDN 地址。

---

### `GET /api/flow/projects/{project_id}`

获取单个项目的元信息。

**路径参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `project_id` | string (UUID) | 项目 ID |

**响应示例：**
```json
{
  "projectId": "2bc7c7bc-eb18-4975-9deb-6f93c3df3afb",
  "projectInfo": {
    "projectTitle": "出图-01",
    "thumbnailMediaKey": "90f3a512-..."
  },
  "agentInfo": {
    "defaultGenerationSettings": {
      "imageDefaults": { "imageModelFamilyKey": "narwhal_display" },
      "videoDefaults": { "videoModelFamilyKey": "abra" }
    },
    "agentToggleState": "AGENT_TOGGLE_STATE_DISABLED"
  }
}
```

**错误码：** `404` — 项目不存在。

---

## 3. Google Flow 媒体查询

### `GET /api/flow/projects/{project_id}/media`

获取指定项目的**所有媒体**（图片和视频）。

**路径参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `project_id` | string (UUID) | 项目 ID |

**查询参数：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `limit` | int | 200 | 从历史记录中获取的最大条数（实际返回数取决于 API 限制，建议传 20） |

**响应示例：**
```json
{
  "project_id": "2bc7c7bc-eb18-4975-9deb-6f93c3df3afb",
  "total": 14,
  "media": [
    {
      "mediaKey": "34df4637-779f-471e-b10d-2e6a4062010b",
      "mediaType": "IMAGE",
      "prompt": "A minimalist studio photo of a red apple on a white table, soft light",
      "modelName": "HARBOR_SEAL",
      "aspectRatio": "IMAGE_ASPECT_RATIO_LANDSCAPE",
      "workflowId": "3e62da43-ddd6-44cc-832c-4fa6e084f247",
      "createTime": "2026-08-10T07:37:21.475965Z"
    }
  ]
}
```

| 字段路径 | 类型 | 说明 |
|----------|------|------|
| `media[].mediaKey` | string (UUID) | 媒体唯一 ID，用于获取签名 URL 或传给其他端点 |
| `media[].mediaType` | string | `"IMAGE"` 或 `"VIDEO"` |
| `media[].prompt` | string | 生成该媒体时使用的原始 prompt |
| `media[].modelName` | string | 使用的 AI 模型名称（如 `NARWHAL`, `HARBOR_SEAL`, `GEM_PIX_2`） |
| `media[].aspectRatio` | string | 纵横比（如 `IMAGE_ASPECT_RATIO_LANDSCAPE`, `IMAGE_ASPECT_RATIO_PORTRAIT`, `IMAGE_ASPECT_RATIO_SQUARE`） |
| `media[].workflowId` | string (UUID) | 工作流 ID |
| `media[].createTime` | string (ISO 8601) | 创建时间 |

> **获取图片签名 URL：** 使用 `GET /api/flow/media/{mediaKey}` 或直接访问
> `https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={mediaKey}`

---

### `GET /api/flow/history`

获取用户的媒体生成历史（跨所有项目）。

**查询参数：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `limit` | int | 100 | 最大返回条数（建议 ≤ 20，过大可能失败） |
| `history_type` | string | `"FLOW"` | 历史类型枚举：`FLOW`, `IMAGE_FX`, `VIDEO_FX`, `MUSIC_FX`, `BACKBONE`, `MUSIC_FX_DJ` |

**响应示例：**
```json
{
  "total": 20,
  "workflows": [
    {
      "name": "CAMS...(base64 编码内部 ID)",
      "media": {
        "name": "CAMS...",
        "image": {
          "seed": 434004,
          "prompt": "...",
          "modelNameType": "HARBOR_SEAL",
          "workflowId": "3e62da43-...",
          "aspectRatio": "IMAGE_ASPECT_RATIO_SQUARE"
        },
        "mediaGenerationId": {
          "mediaType": "IMAGE",
          "projectId": "2bc7c7bc-...",
          "workflowId": "3e62da43-...",
          "workflowStepId": "CAE",
          "mediaKey": "34df4637-..."
        },
        "requestData": {
          "promptInputs": [...],
          "imageGenerationRequestData": { ... }
        }
      },
      "createTime": "2026-08-10T07:37:21.475965Z"
    }
  ]
}
```

> **说明：** 这是最完整的媒体历史端点。每个 `workflow` 包含完整的 `media` 对象，
> 其中 `media.mediaGenerationId.projectId` 可用于按项目过滤，
> `media.mediaGenerationId.mediaKey` 是 UUID 格式的 media ID。

---

### `GET /api/flow/media/{media_id}`

获取单个媒体的元数据 + 签名 URL。

**路径参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `media_id` | string (UUID) | 媒体 ID（mediaKey） |

**响应：** 返回 Google Flow 的原始媒体元数据（包含签名 URL）。

---

## 4. 图片生成 & 编辑

### `POST /api/flow/generate-image`

生成图片（绕过队列，直接调用 Google Flow API）。

**请求体 (JSON)：**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `prompt` | string | ✅ | — | 图片生成 prompt |
| `project_id` | string (UUID) | ✅ | — | 目标项目 ID |
| `aspect_ratio` | string | ❌ | `"IMAGE_ASPECT_RATIO_PORTRAIT"` | 纵横比 |
| `user_paygate_tier` | string | ❌ | `"PAYGATE_TIER_ONE"` | 用户付费等级 |
| `character_media_ids` | string[] | ❌ | null | 角色参考图的 media ID 列表 |
| `image_model` | string | ❌ | null | 覆盖模型（如 `GEM_PIX_2_UPSAMPLE_2K`） |
| `source_media_id` | string | ❌ | null | 编辑/放大的源图片 media ID |

**纵横比枚举值：**
- `IMAGE_ASPECT_RATIO_PORTRAIT` — 竖版
- `IMAGE_ASPECT_RATIO_LANDSCAPE` — 横版
- `IMAGE_ASPECT_RATIO_SQUARE` — 正方形
- `IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE` — 4:3 横版

**请求示例：**
```json
{
  "prompt": "A red apple on a white table, studio photo",
  "project_id": "2bc7c7bc-eb18-4975-9deb-6f93c3df3afb",
  "aspect_ratio": "IMAGE_ASPECT_RATIO_LANDSCAPE"
}
```

**响应：** Google Flow 的原始生成结果（包含 operations 或直接返回生成的媒体数据）。

---

### `POST /api/flow/edit-image`

编辑已有图片（使用参考图重新生成）。

**请求体 (JSON)：**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `prompt` | string | ✅ | — | 编辑 prompt |
| `source_media_id` | string (UUID) | ✅ | — | 源图片的 media ID |
| `project_id` | string (UUID) | ✅ | — | 项目 ID |
| `aspect_ratio` | string | ❌ | `"IMAGE_ASPECT_RATIO_PORTRAIT"` | 纵横比 |
| `user_paygate_tier` | string | ❌ | `"PAYGATE_TIER_ONE"` | 付费等级 |

---

### `POST /api/flow/upscale-image`

将图片放大至 2K/4K 分辨率。

**请求体 (JSON)：**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `media_id` | string (UUID) | ✅ | — | 要放大的图片 media ID |
| `project_id` | string | ❌ | `""` | 项目 ID |
| `target_resolution` | string | ❌ | `"UPSAMPLE_IMAGE_RESOLUTION_4K"` | 目标分辨率 |
| `user_paygate_tier` | string | ❌ | `"PAYGATE_TIER_TWO"` | 付费等级 |

**目标分辨率枚举：**
- `UPSAMPLE_IMAGE_RESOLUTION_2K`
- `UPSAMPLE_IMAGE_RESOLUTION_4K`

**响应示例：**
```json
{
  "media_id": "...",
  "base64": "iVBORw0KGgo...(base64 编码的图片数据)",
  "size_bytes": 2456789,
  "resolution": "UPSAMPLE_IMAGE_RESOLUTION_4K"
}
```

---

## 5. 视频生成 & 管理

### `POST /api/flow/generate-video`

提交视频生成任务（首帧 → 视频）。

**请求体 (JSON)：**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `start_image_media_id` | string (UUID) | ✅ | — | 首帧图片的 media ID |
| `prompt` | string | ✅ | — | 视频 prompt |
| `project_id` | string (UUID) | ✅ | — | 项目 ID |
| `scene_id` | string | ✅ | — | 场景 ID |
| `aspect_ratio` | string | ❌ | `"VIDEO_ASPECT_RATIO_PORTRAIT"` | 纵横比 |
| `end_image_media_id` | string (UUID) | ❌ | null | 末帧图片 media ID（关键帧模式） |
| `user_paygate_tier` | string | ❌ | `"PAYGATE_TIER_ONE"` | 付费等级 |

**视频纵横比枚举：**
- `VIDEO_ASPECT_RATIO_PORTRAIT` — 竖版
- `VIDEO_ASPECT_RATIO_LANDSCAPE` — 横版

---

### `POST /api/flow/generate-video-refs`

通过参考图片生成视频（R2V 模式）。

**请求体 (JSON)：**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `reference_media_ids` | string[] | ✅ | — | 参考图片的 media ID 列表 |
| `prompt` | string | ✅ | — | 视频 prompt |
| `project_id` | string (UUID) | ✅ | — | 项目 ID |
| `scene_id` | string | ✅ | — | 场景 ID |
| `aspect_ratio` | string | ❌ | `"VIDEO_ASPECT_RATIO_PORTRAIT"` | 纵横比 |
| `user_paygate_tier` | string | ❌ | `"PAYGATE_TIER_ONE"` | 付费等级 |

---

### `POST /api/flow/upscale-video`

提交视频超分任务。

**请求体 (JSON)：**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `media_id` | string (UUID) | ✅ | — | 视频 media ID |
| `scene_id` | string | ✅ | — | 场景 ID |
| `aspect_ratio` | string | ❌ | `"VIDEO_ASPECT_RATIO_PORTRAIT"` | 纵横比 |
| `resolution` | string | ❌ | `"VIDEO_RESOLUTION_4K"` | 目标分辨率 |

---

### `POST /api/flow/check-status`

检查视频生成/超分任务状态。

**请求体 (JSON)：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `operations` | object[] | ✅ | 由 `generate-video` / `upscale-video` 返回的 operations 数组 |

---

## 6. 图片上传

### `POST /api/flow/upload-image-data`

上传图片数据（base64 或 data URL）到 Google Flow。

**请求体 (JSON)：**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `data_url` | string | ✅ | — | `data:image/png;base64,...` 或纯 base64 字符串 |
| `project_id` | string | ❌ | `""` | 关联项目 ID |
| `file_name` | string | ❌ | `"image.png"` | 文件名 |

**响应示例：**
```json
{
  "media_id": "618b0a0e-48d3-486d-9b12-149e71816593",
  "raw": { ... }
}
```

---

### `POST /api/flow/upload-image`

上传本地文件系统中的图片到 Google Flow。

**请求体 (JSON)：**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `file_path` | string | ✅ | — | 图片的绝对路径 |
| `project_id` | string | ❌ | `""` | 关联项目 ID |
| `file_name` | string | ❌ | `"image.png"` | 文件名 |

**响应示例：**
```json
{
  "media_id": "618b0a0e-48d3-486d-9b12-149e71816593",
  "raw": { ... }
}
```

---

## 7. 媒体下载 & 代理

### `GET /api/flow/media/image/download`

代理下载 Flow CDN 上的图片。

**查询参数：**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `url` | string | ✅ | — | CDN 签名 URL（必须来自 `flow-content.google` 或 `storage.googleapis.com`） |
| `name` | string | ❌ | `"image.jpg"` | 下载文件名 |

**响应：** 二进制图片数据（`Content-Disposition: attachment`）。

---

### `POST /api/flow/refresh-urls/{project_id}`

批量刷新项目内所有媒体的签名 URL。

**路径参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `project_id` | string (UUID) | 项目 ID |

---

## 8. 高级 / 调试

### `POST /api/flow/trpc-proxy`

通用 tRPC 代理——通过 Chrome 扩展向 Google Flow 发送任意 tRPC 请求。

**请求体 (JSON)：**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `procedure` | string | ✅ | — | tRPC 过程名（如 `"project.getProject"`） |
| `input` | object | ❌ | `{}` | tRPC 输入 JSON |
| `method` | string | ❌ | `"GET"` | HTTP 方法 `"GET"` 或 `"POST"` |

**请求示例：**
```json
{
  "procedure": "project.searchUserProjects",
  "input": { "json": { "toolName": "PINHOLE", "pageSize": 10 } },
  "method": "GET"
}
```

**响应：** Google Flow tRPC 端点的原始响应。

---

### `POST /api/flow/test-resolve-media`

测试 media ID 的 URL 解析（调试用）。

**请求体 (JSON)：**
```json
{ "media_id": "34df4637-779f-471e-b10d-2e6a4062010b" }
```

---

## 9. 前端集成指南

### 获取项目列表并显示

```typescript
// 获取所有 Flow 项目
const res = await fetch('/api/flow/projects');
const { projects } = await res.json();

// projects 数组结构：
// { projectId, projectInfo: { projectTitle, thumbnailMediaKey }, creationTime }
```

### 获取项目内的媒体

```typescript
// 获取项目所有媒体（建议 limit=20）
const res = await fetch(`/api/flow/projects/${projectId}/media?limit=20`);
const { media, total } = await res.json();

// media 数组结构：
// { mediaKey, mediaType, prompt, modelName, aspectRatio, workflowId, createTime }
```

### 构建图片预览 URL

```typescript
// 方法 1：直接用 getMediaUrlRedirect（302 重定向）
const imgUrl = `https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name=${mediaKey}`;

// 方法 2：通过 FlowKit 代理获取签名 URL
const res = await fetch(`/api/flow/media/${mediaKey}`);
const data = await res.json();
// data 中包含 fifeUrl / servingUri
```

### 生成新图片

```typescript
const res = await fetch('/api/flow/generate-image', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    prompt: 'A red apple on a white table',
    project_id: '2bc7c7bc-...',
    aspect_ratio: 'IMAGE_ASPECT_RATIO_LANDSCAPE',
  }),
});
```

### 错误处理

所有端点的错误响应格式：
```json
{
  "detail": "错误描述"
}
```

| HTTP 状态码 | 含义 |
|-------------|------|
| `400` | 请求参数错误 |
| `404` | 资源不存在 |
| `502` | Google Flow 后端错误 |
| `503` | Chrome 扩展未连接 |

---

## 附录：已确认的 Google Flow tRPC 端点

以下端点经过逆向工程验证，是 FlowKit 通过 Chrome 扩展代理调用的底层接口：

| tRPC 过程名 | 方法 | 必填参数 | 说明 |
|-------------|------|----------|------|
| `project.searchUserProjects` | GET | `toolName: "PINHOLE"`, `pageSize: number` (≤10) | 列出项目 |
| `project.getProject` | GET | `projectId`, `toolName: "PINHOLE"` | 获取项目详情 |
| `project.createProject` | POST | `projectTitle`, `toolName: "PINHOLE"` | 创建项目 |
| `media.fetchUserHistory` | GET | `toolName: "PINHOLE"`, `limit: number` (≤20), `type: "FLOW"` | 用户媒体历史 |
| `media.getMediaUrlRedirect` | GET (非标准) | URL 参数 `name={mediaId}` | 302 重定向到 CDN |
| `media.fetchMedia` | GET | `mediaKey` | 获取单条媒体（需特殊格式） |
| `project.searchProjectScenes` | GET | `projectId`, `toolName`, `cursor` | 搜索场景（参数未完全破解） |

> **注意：** `pageSize` 超过 10 或 `limit` 超过 20 可能导致 `400 Bad Request`。
