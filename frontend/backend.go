package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

type backendEvent struct {
	Type           string    `json:"type"`
	ScenarioID     int       `json:"scenario_id,omitempty"`
	Scenario       string    `json:"scenario,omitempty"`
	GroupID        int       `json:"group_id,omitempty"`
	Sender         string    `json:"sender,omitempty"`
	Role           string    `json:"role,omitempty"`
	Content        string    `json:"content,omitempty"`
	Generation     int       `json:"generation,omitempty"`
	BestScore      float64   `json:"best_score,omitempty"`
	BestPrompt     string    `json:"best_prompt,omitempty"`
	BestGenome     string    `json:"best_genome_id,omitempty"`
	Rubric         string    `json:"rubric,omitempty"`
	AvgScore       float64   `json:"avg_score,omitempty"`
	Diversity      float64   `json:"diversity,omitempty"`
	Overall        float64   `json:"overall,omitempty"`
	GenomeID       string    `json:"genome_id,omitempty"`
	ScenarioScores []float64 `json:"scenario_scores,omitempty"`
	ParentIDs      []string  `json:"parent_ids,omitempty"`
	Survivors      []string  `json:"survivors,omitempty"`
	Eliminated     []string  `json:"eliminated,omitempty"`
	Operation      string    `json:"operation,omitempty"`
	Child          string    `json:"child,omitempty"`
	Parent         string    `json:"parent,omitempty"`
	ParentA        string    `json:"parent_a,omitempty"`
	ParentB        string    `json:"parent_b,omitempty"`
	PopSize        int       `json:"population_size,omitempty"`
	NumGens        int       `json:"num_generations,omitempty"`
	NumScenarios   int       `json:"num_scenarios,omitempty"`
	Goal           string    `json:"goal,omitempty"`
}

var FinalResult *EvolutionResult
var CurrentGeneration int
var CurrentBestScore float64
var ActivityLog []string

func logActivity(msg string) {
	ActivityLog = append(ActivityLog, msg)
	if len(ActivityLog) > 50 {
		ActivityLog = ActivityLog[len(ActivityLog)-50:]
	}
}

type GenerationStats struct {
	BestScore  float64
	AvgScore   float64
	Diversity  float64
	Survivors  []string
	Eliminated []string
}

type AgentScore struct {
	GenomeID       string
	Overall        float64
	ScenarioScores []float64
	ParentIDs      []string
	Generation     int
}

type EvolutionResult struct {
	BestPrompt     string
	BestScore      float64
	BestGenome     string
	Goal           string
	Rubric         string
	PopSize        int
	NumGens        int
	Generations    []GenerationStats
	FinalScores    []AgentScore
	AllScores      []AgentScore // all agents across all generations
	ScenarioNames  []string
}

func RunBackend(cells []cell, goal, rubricHint string, generations, population int) {
	go func() {
		defer func() {
			for i := range cells {
				select {
				case <-cells[i].conv.done:
				default:
					close(cells[i].conv.done)
				}
			}
		}()

		logFile, err := os.Create("backend.log")
		if err != nil {
			return
		}
		defer logFile.Close()

		backendDir := filepath.Join("..", "backend")
		pythonPath := filepath.Join(backendDir, "..", ".venv", "bin", "python")

		args := []string{
			"orchestrator.py",
			goal,
			"--population", "6",
			"--generations", fmt.Sprintf("%d", generations),
			"--scenarios", fmt.Sprintf("%d", len(cells)),
		}

		if rubricHint != "" {
			tmpDir, err := os.MkdirTemp("", "agent-kitchen-*")
			if err == nil {
				defer os.RemoveAll(tmpDir)
				rubricPath := filepath.Join(tmpDir, "rubric.txt")
				os.WriteFile(rubricPath, []byte(rubricHint), 0644)
				args = append(args, "--rubric", rubricPath)
			}
		}

		cmd := exec.Command(pythonPath, args...)
		cmd.Dir = backendDir
		cmd.Stderr = logFile
		cmd.Env = append(os.Environ(), "PYTHONDONTWRITEBYTECODE=1")

		stdout, err := cmd.StdoutPipe()
		if err != nil {
			return
		}

		if err := cmd.Start(); err != nil {
			return
		}

		scanner := bufio.NewScanner(stdout)
		scanner.Buffer(make([]byte, 1024*1024), 1024*1024)

		// Track first group per cell for preview, store all for detail tabs
		cellFirstGroup := make(map[int]int)
		cellFirstSet := make(map[int]bool)

		var genStats []GenerationStats
		var finalScores []AgentScore
		var allScores []AgentScore
		var scenarioNames []string
		var evGoal string
		var popSize, numGens int
		var currentSurvivors, currentEliminated []string
		var currentGen int

		for scanner.Scan() {
			var event backendEvent
			if err := json.Unmarshal(scanner.Bytes(), &event); err != nil {
				continue
			}

			switch event.Type {

			case "evolution_start":
				evGoal = event.Goal
				popSize = event.PopSize
				numGens = event.NumGens
				logActivity("LLM generating scenarios...")

			case "scenario_ready":
				scenarioNames = append(scenarioNames, event.Scenario)
				logActivity(fmt.Sprintf("+ %s", event.Scenario))

			case "population_ready":
				logActivity(fmt.Sprintf("%d diverse agents seeded", popSize))
				logActivity("beginning evolution")

			case "generation_start":
				CurrentGeneration = event.Generation
				currentGen = event.Generation
				finalScores = nil
				cellFirstSet = make(map[int]bool)
				cellFirstGroup = make(map[int]int)
				// Don't clear agents — conversations accumulate across gens.
				// New group_ids from new agents will just create new tabs.
				logActivity(fmt.Sprintf("── gen %d ──", event.Generation))
				logActivity("all scenarios running in parallel")
				for i := range cells {
					header := fmt.Sprintf("\n── Generation %d ──\n\n", event.Generation)
					select {
					case cells[i].conv.ch <- header:
					default:
					}
				}

			case "scenario_start":
				idx := event.ScenarioID
				if idx < len(cells) {
					cells[idx].scenario = event.Scenario
					for len(scenarioNames) <= idx {
						scenarioNames = append(scenarioNames, "")
					}
					scenarioNames[idx] = event.Scenario
				}

			case "evaluation_start":
				logActivity("scoring transcripts (3x avg)")

			case "message":
				idx := event.ScenarioID
				if idx >= len(cells) {
					continue
				}
				// Clean up JSON-wrapped messages
				// Track first group for preview
				if !cellFirstSet[idx] {
					cellFirstGroup[idx] = event.GroupID
					cellFirstSet[idx] = true
				}

				// Register this group if new
				if _, exists := cells[idx].agents[event.GroupID]; !exists {
					b := &strings.Builder{}
					cells[idx].agents[event.GroupID] = b
					cells[idx].agentIDs = append(cells[idx].agentIDs, event.GroupID)
				}

				content := event.Content
				// Clean up JSON-wrapped messages
				if strings.HasPrefix(content, "{") {
					var wrapper map[string]string
					if err := json.Unmarshal([]byte(content), &wrapper); err == nil {
						if msg, ok := wrapper["message"]; ok {
							content = msg
						}
					}
				}
				// Flatten to single line — these are chat messages, not emails
				content = strings.ReplaceAll(content, "\\n", " ")
				content = strings.ReplaceAll(content, "\n", " ")
				for strings.Contains(content, "  ") {
					content = strings.ReplaceAll(content, "  ", " ")
				}
				content = strings.TrimSpace(content)

				// Use block markers: >>>\n...\n<<< for agent, <<<name\n...\n<<< for counterparty
				var line string
				if event.Role == "negotiator" {
					line = ">>>\n" + content + "\n<<<\n"
				} else {
					line = "<<<" + event.Sender + "\n" + content + "\n<<<\n"
				}

				// Store in per-agent buffer
				cells[idx].agents[event.GroupID].WriteString(line)

				// Feed preview channel only for first agent
				if event.GroupID == cellFirstGroup[idx] {
					select {
					case cells[idx].conv.ch <- line:
					default:
					}
				}

			case "score":
				as := AgentScore{
					GenomeID:       event.GenomeID,
					Overall:        event.Overall,
					ScenarioScores: event.ScenarioScores,
					ParentIDs:      event.ParentIDs,
					Generation:     currentGen,
				}
				finalScores = append(finalScores, as)
				allScores = append(allScores, as)
				id := event.GenomeID
				if len(id) > 8 {
					id = id[:8]
				}
				logActivity(fmt.Sprintf("  %s → %.0f%%", id, event.Overall*100))

			case "selection":
				currentSurvivors = event.Survivors
				currentEliminated = event.Eliminated
				logActivity(fmt.Sprintf("  %d kept, %d replaced", len(event.Survivors), len(event.Eliminated)))

			case "breed":
				switch event.Operation {
				case "mutate":
					logActivity(fmt.Sprintf("  %s ← mutate(%s)", event.Child, event.Parent))
				case "crossover":
					logActivity(fmt.Sprintf("  %s ← cross(%s, %s)", event.Child, event.ParentA, event.ParentB))
				case "clone":
					logActivity(fmt.Sprintf("  %s ← clone(%s)", event.Child, event.Parent))
				}

			case "generation_complete":
				genStats = append(genStats, GenerationStats{
					BestScore:  event.BestScore,
					AvgScore:   event.AvgScore,
					Diversity:  event.Diversity,
					Survivors:  currentSurvivors,
					Eliminated: currentEliminated,
				})
				currentSurvivors = nil
				currentEliminated = nil
				CurrentBestScore = event.BestScore
				logActivity(fmt.Sprintf("gen %d best: %.0f%%", event.Generation, event.BestScore*100))
				for i := range cells {
					divider := fmt.Sprintf("\n══ Gen %d done · best: %.0f%% ══\n\n",
						event.Generation, event.BestScore*100)
					select {
					case cells[i].conv.ch <- divider:
					default:
					}
				}

			case "evolution_complete":
				FinalResult = &EvolutionResult{
					BestPrompt:    event.BestPrompt,
					BestScore:     event.BestScore,
					BestGenome:    event.BestGenome,
					Goal:          evGoal,
					Rubric:        event.Rubric,
					PopSize:       popSize,
					NumGens:       numGens,
					Generations:   genStats,
					FinalScores:   finalScores,
					AllScores:     allScores,
					ScenarioNames: scenarioNames,
				}
			}
		}

		cmd.Wait()
	}()
}
