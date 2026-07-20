import {
  BookOpen,
  Boxes,
  Code2,
  FileText,
  GitBranch,
  Globe,
  type LucideIcon,
  PlayCircle,
  Rocket,
  Settings2,
  Sparkles,
  Terminal,
  Users,
  Wrench,
} from "lucide-react"

export interface GuideNavItem {
  key: string
  labelZh: string
  labelEn: string
  icon?: LucideIcon
  children?: GuideNavItem[]
  collapsed?: boolean
}

export const guideNav: GuideNavItem[] = [
  {
    key: "getting-started",
    labelZh: "入门指南",
    labelEn: "Getting Started",
    icon: Rocket,
    children: [
      {
        key: "guides/installation",
        labelZh: "安装",
        labelEn: "Installation",
      },
      {
        key: "guides/quickstart",
        labelZh: "快速开始",
        labelEn: "Quick Start",
      },
      {
        key: "guides/configuration",
        labelZh: "配置",
        labelEn: "Configuration",
        icon: Settings2,
      },
      {
        key: "guides/memory",
        labelZh: "长期记忆",
        labelEn: "Long-term Memory",
      },
    ],
  },
  {
    key: "architecture",
    labelZh: "架构",
    labelEn: "Architecture",
    icon: Wrench,
    children: [
      {
        key: "architecture/overview",
        labelZh: "概览",
        labelEn: "Overview",
      },
      {
        key: "architecture/data-flow",
        labelZh: "数据流",
        labelEn: "Data Flow",
      },
      {
        key: "guides/orchestration",
        labelZh: "编排方法",
        labelEn: "Orchestration Methods",
      },
    ],
  },
  {
    key: "cli-tools",
    labelZh: "命令行与工具",
    labelEn: "CLI & Tools",
    icon: Terminal,
    children: [
      {
        key: "guides/cli",
        labelZh: "CLI 参考",
        labelEn: "CLI Reference",
      },
      {
        key: "guides/auto-builder",
        labelZh: "自动构建",
        labelEn: "Auto Builder",
      },
      {
        key: "guides/advisor",
        labelZh: "顾问",
        labelEn: "Advisor",
      },
      {
        key: "guides/recommender",
        labelZh: "推荐器",
        labelEn: "Recommender",
      },
    ],
  },
  {
    key: "examples",
    labelZh: "示例",
    labelEn: "Examples",
    icon: PlayCircle,
    children: [
      {
        key: "examples/index",
        labelZh: "概览",
        labelEn: "Overview",
      },
      {
        key: "examples/sorting",
        labelZh: "排序",
        labelEn: "Sorting",
      },
      {
        key: "examples/tsp",
        labelZh: "TSP",
        labelEn: "TSP",
      },
      {
        key: "examples/lunarlander",
        labelZh: "LunarLander",
        labelEn: "LunarLander",
      },
      {
        key: "examples/symbolic-regression",
        labelZh: "符号回归",
        labelEn: "Symbolic Regression",
      },
      {
        key: "examples/ml-hyperpara",
        labelZh: "ML 超参数",
        labelEn: "ML Hyperparameter",
      },
    ],
  },
  {
    key: "advanced",
    labelZh: "进阶",
    labelEn: "Advanced",
    icon: Sparkles,
    collapsed: true,
    children: [
      {
        key: "advanced-components",
        labelZh: "组件定制",
        labelEn: "Component Customization",
        icon: Boxes,
        children: [
          {
            key: "guides/providers",
            labelZh: "模型供应商",
            labelEn: "Providers",
          },
          {
            key: "guides/evaluators",
            labelZh: "评估器",
            labelEn: "Evaluators",
          },
          {
            key: "guides/multimodal",
            labelZh: "多模态",
            labelEn: "Multimodal",
          },
          {
            key: "guides/embeddings",
            labelZh: "嵌入与轨迹",
            labelEn: "Embeddings & Trajectory",
          },
          {
            key: "guides/timing-metrics",
            labelZh: "计时与指标",
            labelEn: "Timing & Metrics",
          },
        ],
      },
      {
        key: "advanced-orchestrators",
        labelZh: "编排算法深入",
        labelEn: "Orchestrator Deep Dive",
        icon: GitBranch,
        children: [
          {
            key: "guides/island-ga",
            labelZh: "Island GA",
            labelEn: "Island GA",
          },
          {
            key: "guides/dyca",
            labelZh: "DyCA",
            labelEn: "DyCA",
          },
          {
            key: "guides/meoh",
            labelZh: "MEoH",
            labelEn: "MEoH",
          },
        ],
      },
      {
        key: "guides/advanced",
        labelZh: "其他高级用法",
        labelEn: "Other Topics",
      },
    ],
  },
  {
    key: "web-ui",
    labelZh: "Web UI",
    labelEn: "Web UI",
    icon: Globe,
    collapsed: true,
    children: [
      {
        key: "web-ui/overview",
        labelZh: "概览与部署",
        labelEn: "Overview & Deployment",
      },
      {
        key: "web-ui/frontend-integration",
        labelZh: "前端集成",
        labelEn: "Frontend Integration",
      },
    ],
  },
  {
    key: "api",
    labelZh: "API 参考",
    labelEn: "API Reference",
    icon: Code2,
    collapsed: true,
    children: [
      {
        key: "api/index",
        labelZh: "概览",
        labelEn: "Overview",
      },
      {
        key: "api/config",
        labelZh: "配置",
        labelEn: "Config",
      },
      {
        key: "api/provider",
        labelZh: "供应商",
        labelEn: "Provider",
      },
      {
        key: "api/planner",
        labelZh: "规划器",
        labelEn: "Planner",
      },
      {
        key: "api/coder",
        labelZh: "编码器",
        labelEn: "Coder",
      },
      {
        key: "api/evaluator",
        labelZh: "评估器",
        labelEn: "Evaluator",
      },
      {
        key: "api/orchestrator",
        labelZh: "编排器",
        labelEn: "Orchestrator",
      },
      {
        key: "api/infra",
        labelZh: "基础设施",
        labelEn: "Infrastructure",
      },
      {
        key: "api/utils",
        labelZh: "工具",
        labelEn: "Utilities",
      },
    ],
  },
  {
    key: "contributing",
    labelZh: "贡献指南",
    labelEn: "Contributing",
    icon: Users,
    collapsed: true,
    children: [
      {
        key: "contributing/guidelines",
        labelZh: "规范",
        labelEn: "Guidelines",
      },
      {
        key: "contributing/development",
        labelZh: "开发",
        labelEn: "Development",
      },
      {
        key: "contributing/docker-local",
        labelZh: "Docker 本地启动",
        labelEn: "Docker Local Startup",
      },
      {
        key: "contributing/style",
        labelZh: "代码风格",
        labelEn: "Style Guide",
      },
    ],
  },
  {
    key: "changelog",
    labelZh: "更新日志",
    labelEn: "Changelog",
    icon: FileText,
  },
  {
    key: "license",
    labelZh: "许可证",
    labelEn: "License",
    icon: BookOpen,
  },
]
