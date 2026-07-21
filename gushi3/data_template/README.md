<!-- ============================================
  data_template/README.md
  世界观数据模板框架

  本目录是创建全新世界观的空白框架。
  需要创建新世界时，请复制整个 data_template/ 到 data/，然后按各文件的 HTML 注释指引填写内容。
  保持目录结构不变，文件内容全部可改。
============================================ -->

# data_template — 世界观数据模板框架

## 目录结构

```
data_template/
├── README.md                          ← 本文件
├── B1_worldState/                     ← 世界观核心配置
│   ├── README.md                      ← 世界层说明 + 字段指引
│   └── global.json                    ← 世界规则/地点/状态/演变规则
├── B3_npc/                            ← NPC数据
│   ├── README.md                      ← NPC数据格式说明
│   └── 势力分组/                      ← 按势力分文件夹，每个文件夹放一个.json
├── player/                            ← 玩家初始数据
│   ├── README.md                      ← 玩家数据格式说明
│   └── example.json                   ← 示例文件
└── prompt/                            ← 系统提示词客制化
    ├── manifest.json                  ← 占位符清单
    ├── N3_custom/                     ← 感知层客制化（4个文件）
    ├── N5_custom/                     ← 回应层客制化（1个文件）
    ├── N6_custom/                     ← 更新层客制化（1个文件）
    ├── N6_player_custom/              ← 玩家自主行动客制化（1个文件）
    ├── N9b_custom/                    ← 环境叙事客制化（1个文件）
    └── N9c_custom/                    ← 幕后行为客制化（1个文件）
```

## 创建新世界流程

1. **复制**：`cp -r data_template/ data/`
2. **配置世界**：编辑 `data/B1_worldState/global.json` 填写世界观
3. **创建NPC**：在 `data/B3_npc/` 下按势力分文件夹，每个势力一个JSON文件
4. **配置玩家**：编辑 `data/player/` 下的初始玩家数据
5. **调整提示词**：编辑 `data/prompt/` 下各层的 .md 文件适配叙事风格
6. **修改base_cnf.py**：将 `WORLD_DATA_SELECT` 指向新建的世界key
